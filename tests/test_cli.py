"""post -> collect -> resolve through the CLI with a fake DataForSEO client."""
import argparse
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tracker import __main__ as cli
from tracker import config
from tracker.store import Store, shift


class FakeClient:
    serps = {}      # keyword -> list of URLs to return
    poisoned = set()  # keywords whose FIRST fetch of the day is poisoned

    def __init__(self, *a, **k):
        self.tasks = {}
        self.n = 0

    def post_tasks(self, tasks):
        out = []
        for t in tasks:
            self.n += 1
            tid = "t%d-%s" % (self.n, t["tag"])
            self.tasks[tid] = t
            out.append((tid, t["tag"]))
        FakeClient.last = self
        return out, []

    def tasks_ready(self):
        return list(self.tasks)

    def task_get(self, tid):
        t = self.tasks.pop(tid)
        kw = t["keyword"]
        urls = self.serps[kw]
        if kw in self.poisoned and t["tag"].endswith("first"):
            urls = ["https://spam%d.xyz/" % i for i in range(10)]
        items = [{"type": "organic", "rank_group": i + 1, "url": u} for i, u in enumerate(urls)]
        return {"tasks": [{"status_code": 20000, "result": [{"items": items}]}]}


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "data"))
        self.markets = [{"code": "uk", "name": "UK", "location_code": 2826, "language_code": "en",
                         "keywords": ["kw%d" % i for i in range(12)]}]
        self.settings = dict(config.load_settings(), provider="dataforseo", min_scored_keywords=5, collect_wait_minutes=0)
        FakeClient.serps = {k: ["https://%s-site%d.com/" % (k, j) for j in range(10)] for k in self.markets[0]["keywords"]}
        FakeClient.poisoned = set()
        self.client = None
        patches = [mock.patch.object(cli, "Client", self._client), mock.patch.object(config, "SITE", os.path.join(self.tmp, "site")),
                   mock.patch.object(config, "load_annotations", lambda: [])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _client(self):
        if self.client is None:
            self.client = FakeClient()
        return self.client

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_day(self, d):
        cli.cmd_post(argparse.Namespace(date=d, force=False), self.store, self.settings, self.markets)
        cli.cmd_collect(argparse.Namespace(), self.store, self.settings, self.markets)
        cli.cmd_resolve(argparse.Namespace(date=None), self.store, self.settings, self.markets)

    def test_full_cycle(self):
        d0 = "2026-11-01"
        for i in range(4):
            self.run_day(shift(d0, i))
        FakeClient.poisoned = {"kw3"}
        d4 = shift(d0, 4)
        self.run_day(d4)
        e = self.store.load_day(d4)["markets"]["uk"]["kw3"]
        self.assertEqual(e["status"], "recovered")
        self.assertIsNone(self.store.load_state("pending"))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "site", self.settings["tool_path"], "data", "summary.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "site", self.settings["tool_path"], "badge", "uk.svg")))


if __name__ == "__main__":
    unittest.main()
