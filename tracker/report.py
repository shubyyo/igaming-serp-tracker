"""Rebuild everything public from the day files: index series, frozen monthly references,
site/data/summary.json, site/data/index.csv and the SVG badges.

Rebuilding from scratch on every run means revisions (a quarantine confirmed or discarded
the next day) flow into the published numbers automatically.
"""
import csv
import io
import json
import os
from collections import Counter
from datetime import date, datetime, timezone

from . import badge, config, guard, scoring
from .store import shift

STATUSES = ("clean", "recovered", "confirmed_shift", "quarantined", "discarded", "missing", "suspect")


def _days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def market_series(store, code, days, settings):
    """One point per day for a market, plus per-keyword volatility for each day."""
    lookback = settings.get("lookback_days", 7)
    k = max(1, settings.get("consensus_days", 3))
    min_scored = settings.get("min_scored_keywords", 10)
    min_share = settings.get("min_valid_share", 0.8)
    trim = settings.get("trim", 0.05)
    last = {}  # keyword -> recent verified (day, urls, src), oldest first, one provider only
    seen = Counter()  # keyword -> verified days since the start or the last provider switch
    points = []
    switches = {}
    for d in days:
        day = store.load_day(d)
        mk = (day or {}).get("markets", {}).get(code)
        if not mk:
            continue
        counts = Counter(e["status"] for e in mk.values())
        repaired = sum(1 for e in mk.values() if e.get("repaired"))
        per_kw = {}
        for kw, e in mk.items():
            st = e["status"]
            if st not in guard.VERIFIED and st != "quarantined":
                continue
            hist = last.get(kw, [])
            src = e.get("src")
            if hist and hist[-1][2] and src and hist[-1][2] != src:
                # Different providers see slightly different SERPs: rebase instead of scoring the switch.
                switches.setdefault(src, d)
                hist = []
                seen[kw] = 0
            elif seen[kw] >= k and _days_between(hist[-1][0], d) <= lookback:
                # Warm-up: a keyword is scored once it has k verified days to form a consensus.
                # Consensus of up to k verified days, none older than lookback + k - 1 days.
                base = [h[1] for h in hist if _days_between(h[0], d) <= lookback + k - 1]
                per_kw[kw] = scoring.consensus_volatility(base, e["urls"])
            if st in guard.VERIFIED:
                last[kw] = (hist + [(d, e["urls"], src)])[-k:]
                seen[kw] += 1
        basket = len(mk)
        usable = sum(counts[s] for s in guard.VERIFIED) + counts["quarantined"]
        v = scoring.daily_index(per_kw.values(), trim) if len(per_kw) >= min_scored else None
        points.append({
            "d": d,
            "v": v,
            "n": len(per_kw),
            "provisional": counts["quarantined"] > 0 or counts["suspect"] > 0,
            "degraded": basket == 0 or usable / basket < min_share,
            "counts": {s: counts[s] for s in STATUSES if counts[s]},
            "repaired": repaired,
            "basket": basket,
            "_kw": per_kw,
        })
    market_series.switches = switches
    return points


def global_series(market_points, settings):
    min_markets = settings.get("global_min_markets", 4)
    by_day = {}
    for pts in market_points.values():
        for p in pts:
            if p["v"] is not None and not p["degraded"]:
                by_day.setdefault(p["d"], []).append(p)
    out = []
    for d in sorted(by_day):
        ps = by_day[d]
        if len(ps) < min_markets:
            continue
        out.append({"d": d, "v": round(sum(p["v"] for p in ps) / len(ps), 3), "n": len(ps),
                    "provisional": any(p["provisional"] for p in ps), "degraded": False})
    return out


def reference(points, month, settings, frozen, code=None):
    """P50/P80/P95/P99 for `month` (YYYY-MM): trailing window before the month starts, frozen once made.

    Until a series has `reference_min_days` of its own history, it uses its pre-launch calibration
    (settings["calibration"][code]) or, failing that, the generic bootstrap. Those stand-ins are
    re-derived on every rebuild rather than frozen, so a corrected calibration takes effect.
    """
    if month in frozen and frozen[month].get("source", "").startswith("trailing"):
        return frozen[month]
    start = month + "-01"
    lo = shift(start, -settings.get("reference_window_days", 90))
    vals = [p["v"] for p in points
            if lo <= p["d"] < start and p["v"] is not None and not p["degraded"] and not p["provisional"]]
    if len(vals) >= settings.get("reference_min_days", 30):
        ref = {k: round(scoring.percentile(vals, q), 3) for k, q in (("p50", 50), ("p80", 80), ("p95", 95), ("p99", 99))}
        ref.update(n=len(vals), source="trailing %d days" % settings.get("reference_window_days", 90))
    elif code in (settings.get("calibration") or {}):
        ref = dict(settings["calibration"][code], n=len(vals))
    else:
        ref = dict(settings["bootstrap_reference"], n=len(vals), source="bootstrap")
    frozen[month] = ref
    return ref


def score_points(points, settings, frozen, code=None):
    for p in points:
        ref = reference(points, p["d"][:7], settings, frozen, code)
        p["s"] = scoring.display_score(p["v"], ref)
        p["b"] = scoring.band(p["v"], ref)


def _public(p):
    out = {"d": p["d"], "v": p["v"], "s": p.get("s"), "b": p.get("b"), "n": p["n"]}
    if p["provisional"]:
        out["p"] = 1
    if p["degraded"]:
        out["g"] = 1
    return out


def _latest(points):
    for p in reversed(points):
        if p["v"] is not None and not p["degraded"]:
            return p
    return points[-1] if points else None


def rebuild(store, markets, settings, annotations, site_dir, today=None):
    days = store.list_days()
    frozen_all = store.load_state("references", {})
    pts, switch_days = {}, {}
    for m in markets:
        pts[m["code"]] = market_series(store, m["code"], days, settings)
        for src, d in market_series.switches.items():
            switch_days[src] = min(d, switch_days.get(src, d))
    annotations = list(annotations) + [
        {"start": d, "end": d, "kind": "method", "label": "Data provider: %s" % src,
         "note": "Keywords are re-baselined on this day; it carries no volatility score for the switch itself."}
        for src, d in sorted(switch_days.items(), key=lambda kv: kv[1])]
    gpts = global_series(pts, settings)

    series = {"global": gpts}
    series.update(pts)
    for code, points in series.items():
        score_points(points, settings, frozen_all.setdefault(code, {}), code)
    store.save_state("references", frozen_all)

    keep = settings.get("publish_days", 365)
    cutoff = shift(today or (days[-1] if days else date.today().isoformat()), -keep)

    def pack(code, name, points, extra=None):
        latest = _latest(points)
        visible = [p for p in points if p["d"] > cutoff]
        out = {
            "code": code,
            "name": name,
            "latest": _public(latest) if latest else None,
            "reference": dict(frozen_all[code][latest["d"][:7]], floor=round(scoring.score_floor(frozen_all[code][latest["d"][:7]]), 3)) if latest else None,
            "series": [_public(p) for p in visible],
        }
        out.update(extra or {})
        return out

    quality_total = Counter()
    out_markets = []
    for m in markets:
        points = pts[m["code"]]
        last30 = [p for p in points if p["d"] > shift(days[-1], -30)] if days else []
        q30 = Counter()
        for p in last30:
            q30.update(p["counts"])
            q30["repaired_by_provider"] += p["repaired"]
        quality_total.update(q30)
        latest = _latest(points)
        movers = sorted((latest or {}).get("_kw", {}).items(), key=lambda kv: -kv[1])[:5]
        out_markets.append(pack(m["code"], m["name"], points, {
            "language": m["language_code"],
            "basket": len(m.get("keywords") or []) or (points[-1]["basket"] if points else 0),
            "today": dict(points[-1]["counts"], repaired_by_provider=points[-1]["repaired"]) if points else {},
            "quality_30d": dict(q30),
            "movers": [{"keyword": k, "v": round(100 * v, 1)} for k, v in movers],
        }))

    summary = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "latest_date": days[-1] if days else None,
        "title": settings.get("title", "iGaming SERP Volatility Index"),
        "site_url": settings.get("site_url"),
        "methodology_url": settings.get("methodology_url"),
        "embed_link_url": settings.get("embed_link_url") or settings.get("methodology_url"),
        "depth": settings.get("depth", 10),
        "device": settings.get("device", "mobile"),
        "global": pack("global", "All markets", gpts),
        "markets": out_markets,
        "quality_30d": dict(quality_total),
        "annotations": annotations,
    }

    tool = config.tool_dir(site_dir, settings)
    data_dir = os.path.join(tool, "data")
    badge_dir = os.path.join(tool, "badge")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(badge_dir, exist_ok=True)
    with open(os.path.join(data_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, separators=(",", ":"))
    _write_csv(os.path.join(data_dir, "index.csv"), series)
    label = settings.get("badge_label", "iGaming SERP")
    for code, points in series.items():
        latest = _latest(points)
        name = "All markets" if code == "global" else code.upper()
        svg = badge.render(("%s %s" % (label, "" if code == "global" else name)).strip(),
                           latest.get("s") if latest else None, latest.get("b") if latest else None,
                           provisional=bool(latest and latest["provisional"]))
        with open(os.path.join(badge_dir, code + ".svg"), "w", encoding="utf-8") as f:
            f.write(svg)
    _write_sitemap(site_dir, settings, summary["latest_date"])
    return summary


def _write_sitemap(site_dir, settings, latest_date):
    """site/sitemap.xml: tools index, this tool (lastmod = latest data day) and its methodology."""
    tool_url, method_url = settings.get("site_url"), settings.get("methodology_url")
    if not tool_url:
        return
    root = "/".join(tool_url.split("/")[:3]) + "/"
    rows = [(root, None), (tool_url, latest_date), (method_url, None)]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in rows:
        if loc:
            out.append("  <url><loc>%s</loc>%s</url>" % (loc, "<lastmod>%s</lastmod>" % lastmod if lastmod else ""))
    out.append("</urlset>")
    with open(os.path.join(site_dir, "sitemap.xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def _write_csv(path, series):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "series", "index_v", "score_0_10", "band", "scored_keywords", "provisional", "degraded"])
    rows = []
    for code, points in series.items():
        for p in points:
            rows.append([p["d"], code, "" if p["v"] is None else p["v"], "" if p.get("s") is None else p["s"],
                         p.get("b") or "", p["n"], int(p["provisional"]), int(p["degraded"])])
    rows.sort()
    w.writerows(rows)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
