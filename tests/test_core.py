import os
import shutil
import tempfile
import unittest
import xml.dom.minidom

from tracker import badge, guard, pipeline, report, scoring
from tracker.canonical import canonical_url, domain_of, is_social_or_forum
from tracker.dataforseo import build_task, parse_organic
from tracker.store import Store, shift


def serp(prefix, n=10, start=0):
    return ["%s%d.example/p" % (prefix, i) for i in range(start, start + n)]


class Canonical(unittest.TestCase):
    def test_strips_noise(self):
        self.assertEqual(canonical_url("https://www.Example.com/Path/?utm_source=x&b=2&a=1#top"), "example.com/Path?a=1&b=2")
        self.assertEqual(canonical_url("http://example.com/"), "example.com")
        self.assertEqual(canonical_url("https://example.com/x?gclid=1&btag=aff"), "example.com/x")
        self.assertIsNone(canonical_url(""))

    def test_host_identities(self):
        from tracker.canonical import identities
        raw = ["https://www.a.com/x?utm_source=1", "https://a.com/y", "http://b.com/", "", "https://a.com/z"]
        self.assertEqual(identities(raw), ["a.com", "a.com#2", "b.com", "a.com#3"])
        self.assertEqual(identities(raw, "url"), ["a.com/x", "a.com/y", "b.com", "a.com/z"])
        self.assertEqual(domain_of("reddit.com#2"), "reddit.com")

    def test_domains(self):
        self.assertEqual(domain_of("sub.example.com/a?b=1"), "sub.example.com")
        self.assertTrue(is_social_or_forum("old.reddit.com"))
        self.assertFalse(is_social_or_forum("notreddit.com"))


class Scoring(unittest.TestCase):
    def test_bounds(self):
        a = serp("a")
        self.assertEqual(scoring.keyword_volatility(a, a), 0.0)
        self.assertAlmostEqual(scoring.keyword_volatility(a, serp("b")), 1.0)

    def test_top_moves_weigh_more(self):
        a = serp("a")
        top = a[:]
        top[0], top[1] = top[1], top[0]
        bottom = a[:]
        bottom[8], bottom[9] = bottom[9], bottom[8]
        self.assertGreater(scoring.keyword_volatility(a, top), scoring.keyword_volatility(a, bottom))

    def test_trimmed_mean(self):
        vals = [0.1] * 18 + [0.0, 10.0]
        self.assertAlmostEqual(scoring.trimmed_mean(vals, 0.05), 0.1)
        self.assertIsNone(scoring.trimmed_mean([]))
        self.assertEqual(scoring.trimmed_mean([5]), 5)

    def test_percentile_band_score(self):
        self.assertEqual(scoring.percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertAlmostEqual(scoring.percentile([0, 10], 25), 2.5)
        ref = {"p50": 2, "p80": 4, "p95": 6, "p99": 8}
        self.assertEqual([scoring.band(v, ref) for v in (1, 3, 5, 7)], ["calm", "normal", "elevated", "storm"])
        self.assertEqual(scoring.display_score(4, 8), 5.0)
        self.assertEqual(scoring.display_score(20, 8), 10.0)


class Guard(unittest.TestCase):
    def setUp(self):
        self.hist = [serp("a")] * 7

    def test_clean_and_cold_start(self):
        self.assertFalse(guard.assess(serp("a"), self.hist)[0])
        self.assertFalse(guard.assess(serp("z"), self.hist[:2])[0])
        self.assertTrue(guard.assess([], self.hist)[0])

    def test_low_overlap_is_suspect(self):
        self.assertTrue(guard.assess(serp("z"), self.hist)[0])

    def test_social_spike(self):
        urls = serp("a", 7) + ["reddit.com/r/x/%d" % i for i in range(3)]
        suspect, info = guard.assess(urls, self.hist)
        self.assertTrue(suspect, info)

    def test_continuity_override(self):
        # Reference is a.* for 6 days, but the last verified day already moved to b.*:
        # today repeating b.* is a continuation, not poisoning.
        hist = [serp("a")] * 6 + [serp("b")]
        suspect, info = guard.assess(serp("b"), hist)
        self.assertFalse(suspect)
        self.assertIn("continuity", info["reason"])

    def test_refetch_paths(self):
        poisoned, other = serp("z"), serp("y")
        self.assertEqual(guard.resolve_refetch(poisoned, serp("a"), self.hist)[0], "recovered")
        self.assertEqual(guard.resolve_refetch(poisoned, poisoned, self.hist)[0], "quarantined")
        self.assertEqual(guard.resolve_refetch(poisoned, other, self.hist)[0], "discarded")
        self.assertTrue(guard.confirm_quarantine(poisoned, poisoned))
        self.assertFalse(guard.confirm_quarantine(poisoned, serp("a")))


class DataForSEO(unittest.TestCase):
    def test_build_and_parse(self):
        t = build_task("casino", {"location_code": 2826, "language_code": "en"}, "tag")
        self.assertEqual((t["device"], t["depth"], t["resolve_poisoned_results"]), ("mobile", 10, True))
        payload = {"tasks": [{"status_code": 20000, "result": [{"items": [
            {"type": "organic", "rank_group": 2, "url": "https://b.com/"},
            {"type": "people_also_ask", "rank_group": 1},
            {"type": "organic", "rank_group": 1, "url": "https://a.com/", "checks": ["from_web"]},
            {"type": "organic", "rank_group": 11, "url": "https://k.com/"},
        ]}]}]}
        urls, repaired, status = parse_organic(payload)
        self.assertEqual(urls, ["https://a.com/", "https://b.com/"])
        self.assertTrue(repaired)
        self.assertEqual(parse_organic({"tasks": [{"status_code": 40602}]})[0], None)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "data"))
        self.d0 = "2026-11-01"
        for i in range(5):
            d = shift(self.d0, i)
            pipeline.ingest_first(self.store, d, "uk", {"kw": (["https://" + u for u in serp("a")], False)})
            pipeline.finalize(self.store, d)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def status(self, d):
        return self.store.load_day(d)["markets"]["uk"]["kw"]["status"]

    def day(self, i, first, second=None):
        d = shift(self.d0, i)
        sus = pipeline.ingest_first(self.store, d, "uk", {"kw": (first, False)})
        if sus:
            pipeline.ingest_refetch(self.store, d, "uk", {"kw": (second, False)})
        pipeline.finalize(self.store, d)
        return d

    def test_real_shift_is_confirmed(self):
        new = ["https://" + u for u in serp("b")]
        d5 = self.day(5, new, new)
        self.assertEqual(self.status(d5), "quarantined")
        d6 = self.day(6, new)
        self.assertEqual(self.status(d5), "confirmed_shift")
        self.assertEqual(self.status(d6), "clean")
        revs = self.store.load_day(d5)["revisions"]
        self.assertEqual((revs[-1]["from"], revs[-1]["to"]), ("quarantined", "confirmed_shift"))

    def test_transient_poisoning_is_discarded(self):
        spam = ["https://" + u for u in serp("z")]
        d5 = self.day(5, spam, spam)
        self.assertEqual(self.status(d5), "quarantined")
        self.day(6, ["https://" + u for u in serp("a")])
        self.assertEqual(self.status(d5), "discarded")

    def test_recovered(self):
        d5 = self.day(5, ["https://" + u for u in serp("z")], ["https://" + u for u in serp("a")])
        self.assertEqual(self.status(d5), "recovered")

    def test_unanswered_refetch_is_discarded(self):
        d = shift(self.d0, 5)
        pipeline.ingest_first(self.store, d, "uk", {"kw": (["https://" + u for u in serp("z")], False)})
        pipeline.finalize(self.store, d)
        self.assertEqual(self.status(d), "discarded")

    def test_report_and_badge(self):
        settings = {"min_scored_keywords": 1, "bootstrap_reference": {"p50": 1, "p80": 2, "p95": 3, "p99": 4},
                    "global_min_markets": 1}
        site = os.path.join(self.tmp, "site")
        s = report.rebuild(self.store, [{"code": "uk", "name": "UK", "language_code": "en"}], settings, [], site)
        self.assertEqual(s["markets"][0]["latest"]["v"], 0.0)
        xml.dom.minidom.parse(os.path.join(site, "badge", "uk.svg"))
        xml.dom.minidom.parseString(badge.render("A & B", None, None))


if __name__ == "__main__":
    unittest.main()
