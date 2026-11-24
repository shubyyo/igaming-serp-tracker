"""python -m tracker {daily,post,collect,resolve,rebuild,simulate,cost,quota}

  daily     hourly     the whole cycle in one job (post -> collect -> wait -> resolve) for today,
                       unless today is already closed. GitHub's cron can start hours late; with one
                       idempotent job triggered every hour, the first run that starts does the day
                       and the rest exit at once.

Provider: settings.json "provider" or the TRACKER_PROVIDER env var (serpapi | dataforseo).

  post      06:00 UTC  DataForSEO: queue today's first fetches. SerpApi: no-op
  collect   07:30 UTC  first fetches (DataForSEO: pull queued tasks; SerpApi: fetch now), run the guard,
                       queue re-fetches of suspects
  resolve   09:00 UTC  re-fetches, settle quarantines, close the day
  quota     any time   SerpApi plan, searches left and how many days they cover
  rebuild   any time   regenerate site/data and badges from data/days
  simulate  offline    synthetic run into ./sim (no API calls)
  cost      offline    print the expected monthly API cost
"""
import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

from . import config, pipeline, report
from .dataforseo import Client, build_task, parse_organic
from .store import Store


def today_utc():
    return datetime.now(timezone.utc).date().isoformat()


def log(*a):
    print("[%s]" % datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def _post(client, store, d, phase, items, settings, markets_by_code):
    """items: [(market_code, keyword)]. Saves the pending state for the next step."""
    tasks, meta = [], {}
    for i, (code, kw) in enumerate(items):
        tag = "%s|%s|%d|%s" % (d, code, i, phase)
        meta[tag] = {"m": code, "k": kw}
        tasks.append(build_task(kw, markets_by_code[code], tag, depth=settings.get("depth", 10),
                                resolve_poison=settings.get("resolve_poisoned_results", True)))
    created, errors = client.post_tasks(tasks) if tasks else ([], [])
    pending = {"date": d, "phase": phase, "posted_at": datetime.now(timezone.utc).isoformat(),
               "tasks": {tid: meta[tag] for tid, tag in created if tag in meta},
               "failed": [meta[tag] for tag, _, _ in errors if tag in meta]}
    store.save_state("pending", pending)
    log("%s: posted %d tasks, %d rejected" % (phase, len(pending["tasks"]), len(pending["failed"])))
    for e in errors[:10]:
        log("  rejected:", e)
    return pending


def _gather(client, pending, settings):
    """Wait for the pending tasks and parse them. Returns {market: {keyword: (raw_urls|None, repaired)}}."""
    want = dict(pending["tasks"])
    got = {}
    deadline = time.time() + 60 * settings.get("collect_wait_minutes", 20)
    while want:
        for tid in [t for t in client.tasks_ready() if t in want]:
            urls, repaired, status = parse_organic(client.task_get(tid), settings.get("depth", 10))
            got[tid] = (urls, repaired)
            want.pop(tid)
        if not want or time.time() > deadline:
            break
        log("waiting for %d tasks" % len(want))
        time.sleep(60)
    for tid in list(want):   # last direct attempt for anything tasks_ready did not list
        urls, repaired, status = parse_organic(client.task_get(tid), settings.get("depth", 10))
        if urls is not None:
            got[tid] = (urls, repaired)
            want.pop(tid)
    if want:
        log("%d tasks never completed; recorded as missing" % len(want))
    out = defaultdict(dict)
    for tid, meta in pending["tasks"].items():
        out[meta["m"]][meta["k"]] = got.get(tid, (None, False))
    for meta in pending.get("failed", []):
        out[meta["m"]][meta["k"]] = (None, False)
    return out


def _all_items(markets):
    items = [(m["code"], kw) for m in markets for kw in m["keywords"]]
    if not items:
        sys.exit("No keywords configured. Fill config/keywords/<market>.txt.")
    return items


def _serpapi(settings):
    from . import serpapi
    return serpapi.Client(workers=settings.get("serpapi_workers", 6))


def _check_serpapi_quota(client, needed):
    acct = client.account()
    left = acct.get("total_searches_left")
    if left is not None and left < needed:
        sys.exit("SerpApi: %s searches left, %d needed for this run. Switch TRACKER_PROVIDER to dataforseo." % (left, needed))
    log("SerpApi %s: %s searches left" % (acct.get("plan_name"), left))


def cmd_post(args, store, settings, markets):
    d = args.date or today_utc()
    if settings["provider"] == "serpapi":
        log("provider serpapi is synchronous: nothing to queue at post time (collect fetches directly)")
        return
    pending = store.load_state("pending")
    if pending and pending.get("date") == d and not args.force:
        sys.exit("Already posted for %s (phase %s). Use --force to post again." % (d, pending["phase"]))
    _post(Client(), store, d, "first", _all_items(markets), settings, {m["code"]: m for m in markets})


def cmd_collect(args, store, settings, markets):
    by_code = {m["code"]: m for m in markets}
    if settings["provider"] == "serpapi":
        d = args.date or today_utc()
        pending = store.load_state("pending")
        if pending and pending.get("date") == d and not args.force:
            sys.exit("Already collected for %s. Use --force to fetch again." % d)
        client = _serpapi(settings)
        items = _all_items(markets)
        _check_serpapi_quota(client, len(items))
        results = client.fetch_many(items, by_code, settings.get("depth", 10))
        suspects = []
        for code, res in sorted(results.items()):
            s = pipeline.ingest_first(store, d, code, res, source="serpapi")
            log("%s %s: %d fetched, %d missing, %d suspect" % (d, code, len(res), sum(1 for r in res.values() if r[0] is None), len(s)))
            suspects += [[code, kw] for kw in s]
        store.save_state("pending", {"date": d, "phase": "refetch", "provider": "serpapi", "items": suspects,
                                     "posted_at": datetime.now(timezone.utc).isoformat()})
        report.rebuild(store, markets, settings, config.load_annotations(), config.SITE)
        return

    pending = store.load_state("pending")
    if not pending or pending.get("phase") != "first":
        sys.exit("Nothing to collect (no first-phase tasks pending).")
    d = pending["date"]
    client = Client()
    results = _gather(client, pending, settings)
    suspects = []
    for code, res in sorted(results.items()):
        s = pipeline.ingest_first(store, d, code, res, source="dataforseo")
        log("%s %s: %d fetched, %d suspect" % (d, code, len(res), len(s)))
        suspects += [(code, kw) for kw in s]
    _post(client, store, d, "refetch", suspects, settings, by_code)
    report.rebuild(store, markets, settings, config.load_annotations(), config.SITE)


def cmd_resolve(args, store, settings, markets):
    pending = store.load_state("pending")
    d = args.date or (pending or {}).get("date") or today_utc()
    if pending and pending.get("provider") == "serpapi" and pending.get("items"):
        client = _serpapi(settings)
        _check_serpapi_quota(client, len(pending["items"]))
        results = client.fetch_many([tuple(i) for i in pending["items"]], {m["code"]: m for m in markets},
                                    settings.get("depth", 10))
        for code, res in sorted(results.items()):
            pipeline.ingest_refetch(store, d, code, res)
            log("%s %s: %d re-fetched" % (d, code, len(res)))
    elif pending and pending.get("phase") == "refetch" and pending.get("tasks"):
        results = _gather(Client(), pending, settings)
        for code, res in sorted(results.items()):
            pipeline.ingest_refetch(store, d, code, res)
            log("%s %s: %d re-fetched" % (d, code, len(res)))
    elif pending and pending.get("phase") == "first":
        log("collect never ran for %s; closing the day without it" % d)
    pipeline.finalize(store, d)
    store.clear_state("pending")
    store.save_state("last_closed", {"date": d})
    report.rebuild(store, markets, settings, config.load_annotations(), config.SITE)
    log("%s closed" % d)


def cmd_daily(args, store, settings, markets):
    d = args.date or today_utc()   # fixed at the start, so a slow run cannot drift into tomorrow
    if (store.load_state("last_closed") or {}).get("date") == d and not args.force:
        log("%s is already closed; nothing to do" % d)
        return
    pending = store.load_state("pending")
    if pending and pending.get("date") != d:
        log("closing %s, left open by an interrupted run" % pending.get("date"))
        cmd_resolve(argparse.Namespace(date=pending.get("date")), store, settings, markets)
        pending = None
    one = argparse.Namespace(date=d, force=args.force)
    if not pending:   # a run killed after collect resumes at the re-fetch instead of fetching again
        cmd_post(one, store, settings, markets)
        cmd_collect(argparse.Namespace(date=d, force=True), store, settings, markets)
    if (store.load_state("pending") or {}).get("phase") == "first":   # DataForSEO: collect not done yet
        cmd_collect(argparse.Namespace(date=d, force=True), store, settings, markets)
    wait = settings.get("refetch_delay_minutes", 30)
    if (store.load_state("pending") or {}).get("items") or (store.load_state("pending") or {}).get("tasks"):
        log("waiting %d minutes before re-fetching suspects" % wait)
        time.sleep(60 * wait)
    cmd_resolve(argparse.Namespace(date=d), store, settings, markets)


def cmd_rebuild(args, store, settings, markets):
    s = report.rebuild(store, markets, settings, config.load_annotations(), config.SITE)
    g = s["global"]["latest"]
    log("rebuilt; latest %s, global %s" % (s["latest_date"], g))


def cmd_simulate(args, store, settings, markets):
    from . import simulate
    out, s = simulate.run(days=args.days, out=args.out, seed=args.seed)
    g = s["global"]["latest"]
    log("simulated %d days into %s" % (args.days, out))
    log("global latest: %s" % g)
    for m in s["markets"]:
        log("  %-3s %s  30d quality %s" % (m["code"], m["latest"], m["quality_30d"]))


def cmd_quota(args, store, settings, markets):
    """SerpApi plan and how many days the remaining searches cover (free call)."""
    acct = _serpapi(settings).account()
    per_day = sum(len(m["keywords"]) for m in markets) * (1 + args.refetch_share)
    left = acct.get("total_searches_left") or 0
    print("plan: %s   searches/month: %s   left this month: %s   total left: %s   rate limit/hour: %s" % (
        acct.get("plan_name"), acct.get("searches_per_month"), acct.get("plan_searches_left"), left,
        acct.get("account_rate_limit_per_hour")))
    print("renews: %s   basket needs ~%.0f searches/day, ~%.0f per 30 days" % (acct.get("plan_renewal_date"), per_day, per_day * 30))
    print("remaining searches cover ~%.1f days" % (left / per_day if per_day else 0))


def cmd_cost(args, store, settings, markets):
    n = sum(len(m["keywords"]) for m in markets) or args.keywords
    base = settings.get("price_per_task_usd", 0.0006)
    pages = max(1, (settings.get("depth", 10) + 9) // 10)
    per_task = base * pages * (2 if settings.get("resolve_poisoned_results", True) else 1)
    daily = n * per_task * (1 + args.refetch_share)
    print("keywords/day: %d   price/task: $%.4f   re-fetch share: %.0f%%" % (n, per_task, 100 * args.refetch_share))
    print("DataForSEO  per day: $%.2f   per 30 days: $%.2f   per year: $%.2f" % (daily, daily * 30, daily * 365))
    print("SerpApi     %.0f searches/day, ~%.0f per 30 days (1 credit each, no_cache)" % (
        n * (1 + args.refetch_share), n * (1 + args.refetch_share) * 30))


def main(argv=None):
    p = argparse.ArgumentParser(prog="tracker", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("daily")
    sp.add_argument("--date")
    sp.add_argument("--force", action="store_true")
    sp = sub.add_parser("post")
    sp.add_argument("--date")
    sp.add_argument("--force", action="store_true")
    sp = sub.add_parser("collect")
    sp.add_argument("--date")
    sp.add_argument("--force", action="store_true")
    sp = sub.add_parser("quota")
    sp.add_argument("--refetch-share", type=float, default=0.05)
    sp = sub.add_parser("resolve")
    sp.add_argument("--date")
    sub.add_parser("rebuild")
    sp = sub.add_parser("simulate")
    sp.add_argument("--days", type=int, default=150)
    sp.add_argument("--out")
    sp.add_argument("--seed", type=int, default=7)
    sp = sub.add_parser("cost")
    sp.add_argument("--keywords", type=int, default=400)
    sp.add_argument("--refetch-share", type=float, default=0.05)
    args = p.parse_args(argv)

    config.load_dotenv()
    config.ensure_dirs()
    store = Store()
    settings = config.load_settings()
    markets = config.load_markets()
    globals()["cmd_" + args.cmd](args, store, settings, markets)


if __name__ == "__main__":
    main()
