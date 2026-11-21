"""Synthetic SERPs for testing the whole pipeline without spending API credit.

It models, per market and keyword:
  * everyday churn (adjacent swaps, the odd new URL entering low),
  * a simulated "core update" window where many SERPs reshuffle and stay reshuffled,
  * a poisoning wave where some fetches return spam/forum-heavy SERPs for a single fetch,
  * provider-side repair (resolve_poisoned_results) on part of the poisoned fetches,
  * the occasional failed task.
The output goes through exactly the same ingest_first / ingest_refetch / finalize / rebuild
functions as the live path.
"""
import os
import random
import shutil
import zlib

from . import config, pipeline, report
from .store import Store, shift

SPAM_HOSTS = ["reddit.com", "quora.com", "facebook.com"]


class World:
    def __init__(self, markets, seed=7, keywords_per_market=50):
        self.rng = random.Random(seed)
        self.serps = {}
        self.pool = {}
        for m in markets:
            for i in range(keywords_per_market):
                key = (m["code"], "%s keyword %02d" % (m["code"], i + 1))
                pool = ["https://www.site%02d.%s.example/%s/page-%d/" % (j, m["code"], key[1].split()[-1], j)
                        for j in range(30)]
                self.pool[key] = pool
                self.serps[key] = pool[:10]

    def keywords(self, code):
        return sorted(k for (c, k) in self.serps if c == code)

    def advance(self, code, intensity):
        """Move every true SERP in a market forward one day."""
        rng = self.rng
        for key in [k for k in self.serps if k[0] == code]:
            s = list(self.serps[key])
            for _ in range(rng.randint(0, 2) + int(intensity * 4)):
                i = rng.randint(0, 8)
                s[i], s[i + 1] = s[i + 1], s[i]
            if rng.random() < 0.08 + intensity * 0.5:
                outside = [u for u in self.pool[key] if u not in s]
                s[rng.randint(5 if intensity < 0.3 else 0, 9)] = rng.choice(outside)
            if intensity >= 0.6 and rng.random() < 0.45:   # big persistent reshuffle
                outside = [u for u in self.pool[key] if u not in s]
                rng.shuffle(outside)
                for i in rng.sample(range(10), 6):
                    s[i] = outside.pop()
                rng.shuffle(s)
            self.serps[key] = s

    def fetch(self, code, kw, poison_rate, repair_rate=0.5, fail_rate=0.004, spam_seed=None):
        rng = self.rng
        if rng.random() < fail_rate:
            return None, False
        true = self.serps[(code, kw)]
        if rng.random() < poison_rate:
            if rng.random() < repair_rate:
                return self._dress(true), True
            srng = random.Random(spam_seed if spam_seed is not None else rng.random())
            spam = ["https://%s/r/casino/comments/%d" % (srng.choice(SPAM_HOSTS), srng.randint(1, 99))
                    for _ in range(3)]
            spam += ["https://spam%d.example.xyz/%s-bonus" % (srng.randint(1, 500), code) for _ in range(5)]
            return self._dress(spam + true[:2]), False
        return self._dress(true), False

    def _dress(self, urls):
        """Add tracking noise the canonicaliser has to remove."""
        out = []
        for u in urls:
            r = self.rng.random()
            if r < 0.1:
                u = u + "?utm_source=serp"
            elif r < 0.15:
                u = u.replace("https://www.", "http://")
            out.append(u)
        return out


def run(days=150, out=None, seed=7, end=None):
    out = out or os.path.join(config.ROOT, "sim")
    data_dir = os.path.join(out, "data")
    site_dir = os.path.join(out, "site")
    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)
    if os.path.isdir(site_dir):
        shutil.rmtree(site_dir)
    shutil.copytree(config.SITE, site_dir, ignore=shutil.ignore_patterns("data", "badge"))

    settings = config.load_settings()
    markets = config.load_markets()
    store = Store(data_dir)
    world = World(markets, seed=seed)
    rng = world.rng

    end = end or __import__("datetime").date.today().isoformat()
    start = shift(end, -(days - 1))
    update = (shift(start, int(days * 0.55)), shift(start, int(days * 0.55) + 9))
    wave = (shift(start, int(days * 0.25)), shift(start, int(days * 0.25) + 14))
    spiky = {m["code"]: rng.random() < 0.4 for m in markets}

    for i in range(days):
        d = shift(start, i)
        in_update = update[0] <= d <= update[1]
        in_wave = wave[0] <= d <= wave[1]
        for m in markets:
            code = m["code"]
            intensity = 0.0
            if in_update:
                intensity = 0.7 if d == update[0] or rng.random() < 0.25 else 0.25
            elif rng.random() < 0.03:
                intensity = 0.3
            world.advance(code, intensity)
            poison = (0.12 if spiky[code] else 0.06) if in_wave else 0.01
            seeds = {kw: zlib.crc32((d + kw).encode()) for kw in world.keywords(code)}
            first = {kw: world.fetch(code, kw, poison, spam_seed=seeds[kw]) for kw in world.keywords(code)}
            suspects = pipeline.ingest_first(store, d, code, first)
            again = {kw: world.fetch(code, kw, poison * 3, spam_seed=seeds[kw] if rng.random() < 0.6 else None)
                     for kw in suspects}
            pipeline.ingest_refetch(store, d, code, again)
        if i < days - 1:   # leave the last day open so the site shows a provisional state
            pipeline.finalize(store, d)

    annotations = [
        {"start": update[0], "end": update[1], "label": "Simulated core update", "kind": "update"},
        {"start": wave[0], "end": wave[1], "label": "Simulated poisoning wave", "kind": "poisoning"},
    ]
    summary = report.rebuild(store, markets, settings, annotations, site_dir, today=end)
    return out, summary
