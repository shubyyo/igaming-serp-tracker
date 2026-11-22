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

    def test_consensus(self):
        a, b = serp("a"), serp("b")
        # A consensus of one is the plain day-over-day comparison.
        self.assertEqual(scoring.consensus_volatility([a], b), scoring.keyword_volatility(a, b))
        self.assertEqual(scoring.consensus_volatility([a, a, a], a), 0.0)
        # A SERP flipping between two sets reads lower against the consensus than against yesterday.
        self.assertLess(scoring.consensus_volatility([a, b, a], b), scoring.keyword_volatility(a, b))
        self.assertEqual(scoring.expected_ranks([a[:2], a[1:3]]), {a[0]: 6.0, a[1]: 1.5, a[2]: 6.5})

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
        # Anchors: floor max(0, 2*2-8)=0 -> 0, P50 -> 5, P80 -> 7, P95 -> 9, P99 -> 10.
        self.assertEqual([scoring.display_score(v, ref) for v in (0, 1, 2, 3, 4, 6, 7, 8, 20)],
                         [0.0, 2.5, 5.0, 6.0, 7.0, 9.0, 9.5, 10.0, 10.0])
        tight = {"p50": 50, "p80": 54, "p95": 57, "p99": 60}  # floor 40
        self.assertEqual([scoring.display_score(v, tight) for v in (30, 45, 50, 56)], [0.0, 2.5, 5.0, 8.3])
        # Score and band always agree.
        for v in (1, 3, 5, 7, 45, 52, 55, 58):
            for r in (ref, tight):
                s, b = scoring.display_score(v, r), scoring.band(v, r)
                self.assertEqual(b, "calm" if s < 5 else "normal" if s < 7 else "elevated" if s < 9 else "storm")
        self.assertIsNone(scoring.display_score(None, ref))


class Guard(unittest.TestCase):
    def setUp(self):
        self.hist = [serp("a")] * 7

    def test_clean_and_cold_start(self):
        self.assertFalse(guard.assess(serp("a"), self.hist)[0])
        self.assertFalse(guard.assess(serp("z"), self.hist[:2])[0])
        self.assertTrue(guard.assess([], self.hist)[0])

    def test_unseen_hosts_are_suspect(self):
        self.assertTrue(guard.assess(serp("z"), self.hist)[0])
        # 7 of 10 hosts never seen in the window: suspect; 6 of 10: not.
        self.assertTrue(guard.assess(serp("a", 3) + serp("z", 7), self.hist)[0])
        self.assertFalse(guard.assess(serp("a", 4) + serp("z", 6), self.hist)[0])

    def test_flipping_between_seen_serps_is_not_suspect(self):
        # Mobile SERPs often alternate between result sets; a set seen this week is not a takeover.
        hist = [serp("a"), serp("b")] * 3 + [serp("a")]
        suspect, info = guard.assess(serp("b"), hist)
        self.assertFalse(suspect, info)
        self.assertEqual(info["novelty"], 0.0)

    def test_social_spike(self):
        urls = serp("a", 7) + ["reddit.com/r/x/%d" % i for i in range(3)]
        suspect, info = guard.assess(urls, self.hist)
        self.assertTrue(suspect, info)

    def test_continuity_override(self):
        # The social/forum spike already appeared on the last verified day:
        # today repeating it is a continuation, not poisoning.
        spiked = serp("a", 7) + ["reddit.com/r/x/%d" % i for i in range(3)]
        hist = [serp("a")] * 6 + [spiked]
        suspect, info = guard.assess(spiked, hist)
        self.assertFalse(suspect)
        self.assertIn("continuity", info["reason"])

    def test_refetch_paths(self):
        poisoned, other = serp("z"), serp("y")
        self.assertEqual(guard.resolve_refetch(poisoned, serp("a"), self.hist)[0], "recovered")
        self.assertEqual(guard.resolve_refetch(poisoned, poisoned, self.hist)[0], "quarantined")
        self.assertEqual(guard.resolve_refetch(poisoned, other, self.hist)[0], "discarded")
        self.assertEqual(guard.resolve_refetch([], [], self.hist)[0], "discarded")
        self.assertEqual(guard.resolve_refetch([], [], [])[0], "discarded")
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

    def test_persisting_change_is_not_locked_out(self):
        # Day 5: a new SERP, and a re-fetch that disagrees -> discarded. Day 6 shows day 5's
        # SERP again: its hosts were seen yesterday, so it is a real change and counts.
        new = ["https://" + u for u in serp("b")]
        d5 = self.day(5, new, ["https://" + u for u in serp("c")])
        self.assertEqual(self.status(d5), "discarded")
        d6 = self.day(6, new)
        self.assertEqual(self.status(d6), "clean")

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

    def test_calibration_replaces_bootstrap_until_own_history(self):
        cal = {"p50": 2.0, "p80": 3.0, "p95": 5.0, "p99": 8.0, "source": "calibration"}
        settings = {"min_scored_keywords": 1, "bootstrap_reference": {"p50": 1, "p80": 2, "p95": 3, "p99": 4},
                    "global_min_markets": 1, "calibration": {"uk": cal}}
        site = os.path.join(self.tmp, "site")
        markets = [{"code": "uk", "name": "UK", "language_code": "en"}]
        s = report.rebuild(self.store, markets, settings, [], site)
        uk = s["markets"][0]["reference"]
        self.assertEqual((uk["source"], uk["p99"], uk["floor"]), ("calibration", 8.0, 0.0))
        self.assertEqual(s["global"]["reference"]["source"], "bootstrap")
        # Stand-ins are not frozen: a corrected calibration applies on the next rebuild.
        settings["calibration"]["uk"] = dict(cal, p99=9.0)
        s = report.rebuild(self.store, markets, settings, [], site)
        self.assertEqual(s["markets"][0]["reference"]["p99"], 9.0)
        # A trailing reference, once made, stays frozen.
        frozen = {"2026-12": {"p50": 1, "p80": 1, "p95": 1, "p99": 1, "source": "trailing 90 days"}}
        self.assertIs(report.reference([], "2026-12", settings, frozen, "uk"), frozen["2026-12"])


if __name__ == "__main__":
    unittest.main()
