"""Day-state pipeline shared by the live (DataForSEO) path and the simulator.

Keyword-day statuses
    clean            first fetch passed the guard
    suspect          first fetch failed the guard; a re-fetch is pending (never published)
    recovered        re-fetch passed the guard; the re-fetch is used
    quarantined      both fetches failed the guard but agree; published as PROVISIONAL
    confirmed_shift  a quarantined day the following day agreed with (a real SERP change)
    discarded        poisoned, contradictory, or a quarantine the next day did not confirm
    missing          the task failed or never returned

Order of a day:  ingest_first (07:30)  ->  ingest_refetch (09:00)  ->  finalize (09:00)
"""
from . import guard
from .canonical import identities
from .store import shift

HISTORY_SCAN_DAYS = 21  # calendar days scanned for the last 7 verified fetches


# Results are identified by host name: Google now hides most destination URLs behind
# google.*/goto redirects, and only the displayed host is reliable across providers.
IDENTITY = "host"


def canon_list(raw_urls, level=None):
    return identities(raw_urls, level or IDENTITY)


def history(store, d, market, keyword, n=guard.REFERENCE_DAYS):
    """URL lists of the last `n` verified days before `d`, oldest first."""
    found = []
    for back in range(1, HISTORY_SCAN_DAYS + 1):
        e = store.entry(shift(d, -back), market, keyword)
        if e and e.get("status") in guard.VERIFIED and e.get("urls"):
            found.append(e["urls"])
            if len(found) == n:
                break
    return list(reversed(found))


class _Run:
    """Tracks which day files changed so each is written once."""

    def __init__(self, store, d):
        self.store, self.d, self.dirty = store, d, set()

    def day(self, d, create=False):
        return self.store.load_day(d, create=create)

    def set_status(self, d, market, keyword, new, reason):
        day = self.day(d)
        e = day["markets"][market][keyword]
        old = e["status"]
        if old == new:
            return
        e["status"] = new
        e["reason"] = reason
        day.setdefault("revisions", []).append(
            {"market": market, "keyword": keyword, "from": old, "to": new, "on": self.d})
        self.dirty.add(d)

    def prev_quarantine(self, market, keyword):
        """Yesterday's entry if it is still an unresolved quarantine."""
        y = shift(self.d, -1)
        e = self.store.entry(y, market, keyword)
        return (y, e) if e and e.get("status") == "quarantined" else (y, None)

    def flush(self):
        for d in sorted(self.dirty):
            self.store.save_day(self.day(d))
        self.dirty.clear()


def ingest_first(store, d, market, results, source=None):
    """First fetches for one market. results: {keyword: (raw_urls | None, repaired)}.

    `source` names the data provider; a change of provider rebases the keyword's comparison.
    Returns the keywords that need a re-fetch.
    """
    run = _Run(store, d)
    day = run.day(d, create=True)
    mk = day["markets"].setdefault(market, {})
    suspects = []
    for kw in sorted(results):
        raw, repaired = results[kw]
        if raw is None:
            mk[kw] = {"status": "missing", "reason": "first fetch failed", "src": source}
            continue
        urls = canon_list(raw)
        y, prev = run.prev_quarantine(market, kw)
        if prev and guard.confirm_quarantine(prev["urls"], urls):
            run.set_status(y, market, kw, "confirmed_shift", "next day agreed (%s)" % d)
            prev = None
        suspect, info = guard.assess(urls, history(store, d, market, kw))
        entry = {"status": "suspect" if suspect else "clean", "urls": urls,
                 "repaired": bool(repaired), "reason": info["reason"], "src": source}
        if suspect:
            entry["first"] = urls
            suspects.append(kw)
        elif prev:
            run.set_status(y, market, kw, "discarded", "next day returned to the prior SERP (%s)" % d)
        mk[kw] = entry
    run.dirty.add(d)
    run.flush()
    return suspects


def ingest_refetch(store, d, market, results):
    """Re-fetches of today's suspect keywords. results: {keyword: (raw_urls | None, repaired)}."""
    run = _Run(store, d)
    day = run.day(d)
    if not day or market not in day["markets"]:
        return
    mk = day["markets"][market]
    for kw in sorted(results):
        e = mk.get(kw)
        if not e or e["status"] != "suspect":
            continue
        raw, repaired = results[kw]
        if raw is None:
            e.update(status="discarded", urls=None, reason="re-fetch failed")
            continue
        second = canon_list(raw)
        y, prev = run.prev_quarantine(market, kw)
        if prev and guard.confirm_quarantine(prev["urls"], second):
            run.set_status(y, market, kw, "confirmed_shift", "next day agreed (%s)" % d)
            prev = None
        status, keep, info = guard.resolve_refetch(e["first"], second, history(store, d, market, kw))
        e.update(status=status, urls=keep, second=second, reason=info["reason"],
                 repaired=bool(e.get("repaired") or repaired))
        if prev:
            run.set_status(y, market, kw, "discarded", "not confirmed on %s" % d)
    run.dirty.add(d)
    run.flush()


def finalize(store, d):
    """Close the day: unresolved suspects and yesterday's unconfirmed quarantines are discarded."""
    run = _Run(store, d)
    day = run.day(d)
    if day:
        for market, mk in day["markets"].items():
            for kw, e in mk.items():
                if e["status"] == "suspect":
                    e.update(status="discarded", urls=None, reason="no re-fetch result")
                    run.dirty.add(d)
    y = shift(d, -1)
    yday = run.day(y)
    if yday:
        for market, mk in yday["markets"].items():
            for kw, e in mk.items():
                if e["status"] == "quarantined":
                    run.set_status(y, market, kw, "discarded", "not confirmed on %s" % d)
    run.flush()
