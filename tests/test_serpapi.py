"""SerpApi parsing, the synchronous collect -> resolve cycle, and the provider-switch rebase."""
import argparse
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tracker import __main__ as cli
from tracker import config, pipeline, report, serpapi
from tracker.store import Store, shift

MARKET = {"code": "uk", "name": "UK", "location_code": 2826, "language_code": "en",
          "google_domain": "google.co.uk", "gl": "uk", "hl": "en"}


class Parse(unittest.TestCase):
    def client(self, payload):
        c = serpapi.Client(api_key="test")
        c._get = lambda path, params: payload
        return c

    def test_organic_order_and_depth(self):
        payload = {"organic_results": [{"position": i, "link": "https://s%d.com/" % i} for i in (3, 1, 2, 11, 12)]}
        urls, repaired = self.client(payload).search("kw", MARKET)
        self.assertEqual(urls, ["https://s1.com/", "https://s2.com/", "https://s3.com/"])
        self.assertFalse(repaired)

    def test_google_redirects_use_displayed_link(self):
        payload = {"organic_results": [
            {"position": 1, "link": "https://www.sunbingo.co.uk/"},
            {"position": 2, "link": "https://www.google.co.uk/goto?url=CAESWAHE", "displayed_link": "https://www.meccabingo.com"},
            {"position": 3, "link": "https://www.google.co.uk/goto?url=CAESXQ", "displayed_link": "https://www.olbg.com › bingo › sites"},
            {"position": 4, "link": "https://www.google.co.uk/goto?url=CAESVw"},
            {"position": 5, "link": "https://play.google.com/store/apps/details?id=x"},
        ]}
        urls, _ = self.client(payload).search("kw", MARKET)
        self.assertEqual(urls, ["https://www.sunbingo.co.uk/", "https://www.meccabingo.com", "https://www.olbg.com",
                                "https://play.google.com/store/apps/details?id=x"])

    def test_errors(self):
        self.assertEqual(self.client({"error": "Google hasn't returned any results for this query."}).search("kw", MARKET)[0], [])
        self.assertIsNone(self.client({"error": "Your searches for the month are exhausted."}).search("kw", MARKET)[0])

    def test_params(self):
        seen = {}
        c = serpapi.Client(api_key="test")
        c._get = lambda path, params: seen.update(params) or {"organic_results": []}
        c.search("best online casino", MARKET)
        self.assertEqual((seen["gl"], seen["google_domain"], seen["device"], seen["no_cache"]),
                         ("uk", "google.co.uk", "mobile", "true"))


class FakeSerpApi:
    poisoned = set()
    calls = 0

    def __init__(self, *a, **k):
        pass

    def account(self):
        return {"plan_name": "Test", "total_searches_left": 10000}

    def fetch_many(self, items, markets_by_code, depth=10):
        out = {}
        for code, kw in items:
            FakeSerpApi.calls += 1
            urls = ["https://%s-site%d.com/" % (kw, j) for j in range(10)]
            if kw in self.poisoned and FakeSerpApi.calls <= 1000 and not getattr(self, "second", False):
                urls = ["https://spam%d.xyz/" % i for i in range(10)]
            out.setdefault(code, {})[kw] = (urls, False)
        self.second = True
        return out


class Cycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "data"))
        self.markets = [dict(MARKET, keywords=["kw%d" % i for i in range(12)])]
        self.settings = dict(config.load_settings(), provider="serpapi", min_scored_keywords=5)
        for p in [mock.patch.object(cli, "_serpapi", lambda s: self.fake),
                  mock.patch.object(config, "SITE", os.path.join(self.tmp, "site")),
                  mock.patch.object(config, "load_annotations", lambda: [])]:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_day(self, d, poisoned=()):
        self.fake = FakeSerpApi()
        self.fake.poisoned = set(poisoned)
        cli.cmd_post(argparse.Namespace(date=d, force=False), self.store, self.settings, self.markets)
        cli.cmd_collect(argparse.Namespace(date=d, force=False), self.store, self.settings, self.markets)
        cli.cmd_resolve(argparse.Namespace(date=None), self.store, self.settings, self.markets)

    def test_recovers_on_refetch(self):
        d0 = "2026-11-01"
        for i in range(4):
            self.run_day(shift(d0, i))
        d4 = shift(d0, 4)
        self.run_day(d4, poisoned={"kw3"})
        e = self.store.load_day(d4)["markets"]["uk"]["kw3"]
        self.assertEqual((e["status"], e["src"]), ("recovered", "serpapi"))
        self.assertIsNone(self.store.load_state("pending"))


class Daily(Cycle):
    def daily(self, d, poisoned=()):
        self.fake = FakeSerpApi()
        self.fake.poisoned = set(poisoned)
        with mock.patch.object(cli.time, "sleep") as sleep:
            cli.cmd_daily(argparse.Namespace(date=d, force=False), self.store, self.settings, self.markets)
        return sleep

    def test_one_run_per_day(self):
        d0 = "2026-11-01"
        for i in range(4):
            self.daily(shift(d0, i))
        d4 = shift(d0, 4)
        sleep = self.daily(d4, poisoned={"kw3"})
        sleep.assert_called_once()                       # waited before re-fetching the suspect
        self.assertEqual(self.store.entry(d4, "uk", "kw3")["status"], "recovered")
        self.assertEqual(self.store.load_state("last_closed"), {"date": d4})
        calls = FakeSerpApi.calls
        self.daily(d4)                                   # a later hourly trigger: nothing to do
        self.assertEqual(FakeSerpApi.calls, calls)

    def test_resumes_after_interrupted_run(self):
        d0 = "2026-11-01"
        self.fake = FakeSerpApi()
        cli.cmd_collect(argparse.Namespace(date=d0, force=False), self.store, self.settings, self.markets)
        calls = FakeSerpApi.calls                        # killed after collect, before resolve
        self.daily(d0)
        self.assertEqual(FakeSerpApi.calls, calls)       # no second first-fetch
        self.assertEqual(self.store.load_state("last_closed"), {"date": d0})
        self.fake = FakeSerpApi()
        cli.cmd_collect(argparse.Namespace(date=shift(d0, 1), force=False), self.store, self.settings, self.markets)
        self.daily(shift(d0, 3))                         # a stale day is closed, then today runs
        self.assertIsNotNone(self.store.load_day(shift(d0, 3)))
        self.assertEqual(self.store.load_state("last_closed"), {"date": shift(d0, 3)})


class ProviderSwitch(unittest.TestCase):
    def test_switch_day_is_rebased_and_annotated(self):
        tmp = tempfile.mkdtemp()
        try:
            store = Store(os.path.join(tmp, "data"))
            urls = ["https://a%d.com/" % i for i in range(10)]
            moved = urls[1:] + urls[:1]
            d0 = "2026-11-01"
            for i in range(8):
                src = "serpapi" if i < 4 else "dataforseo"
                res = {"kw%d" % k: (moved if i == 4 else urls, False) for k in range(12)}
                pipeline.ingest_first(store, shift(d0, i), "uk", res, source=src)
                pipeline.finalize(store, shift(d0, i))
            settings = {"min_scored_keywords": 5, "global_min_markets": 1,
                        "bootstrap_reference": {"p50": 1, "p80": 2, "p95": 3, "p99": 4}}
            s = report.rebuild(store, [dict(MARKET)], settings, [], os.path.join(tmp, "site"))
            series = {p["d"]: p for p in s["markets"][0]["series"]}
            self.assertIsNone(series[shift(d0, 4)]["v"])          # switch day: no score, not a spike
            # Warm-up within the new provider: scored once it has 3 days of its own.
            self.assertEqual([series[shift(d0, i)]["v"] for i in (5, 6)], [None, None])
            self.assertGreater(series[shift(d0, 7)]["v"], 0)      # consensus of the switch day and two more
            self.assertTrue(any(a["kind"] == "method" and a["start"] == shift(d0, 4) for a in s["annotations"]))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
