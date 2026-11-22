"""Volatility scoring.

Per keyword-day, on verified top-10 canonical URLs (missing = position 11):
    w(r)  = 1 / log2(1 + r),   r = the better of the two ranks
    D_k   = sum over URLs in either day of w(r) * |rank_t - rank_t-1|
    Dmax  = 2 * sum_{r=1..10} w(r) * (11 - r)      (complete replacement)
    v_k   = min(1, D_k / Dmax)
Daily index V = 5%-trimmed mean of v_k over valid keywords, scaled 0-100.
Display score S: piecewise linear through the series' own frozen percentiles,
    lo -> 0, P50 -> 5, P80 -> 7, P95 -> 9, P99 -> 10   (lo = max(0, 2 * P50 - P99)),
so the score and the band always agree: calm < 5 <= normal < 7 <= elevated < 9 <= storm.
"""
import math

DEPTH = 10
MISSING = DEPTH + 1


def weight(rank):
    return 1.0 / math.log2(1 + rank)


DMAX = 2 * sum(weight(r) * (MISSING - r) for r in range(1, DEPTH + 1))


def keyword_volatility(prev_urls, curr_urls):
    """v_k in [0, 1] from two ordered lists of canonical URLs (index 0 = rank 1)."""
    prev = {u: i + 1 for i, u in enumerate(prev_urls[:DEPTH])}
    curr = {u: i + 1 for i, u in enumerate(curr_urls[:DEPTH])}
    d = 0.0
    for url in set(prev) | set(curr):
        a, b = prev.get(url, MISSING), curr.get(url, MISSING)
        if a != b:
            d += weight(min(a, b)) * abs(a - b)
    return min(1.0, d / DMAX)


def trimmed_mean(values, trim=0.05):
    """Mean after dropping `trim` of the values from each end (at least nothing)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = int(len(vals) * trim)
    core = vals[k:len(vals) - k] if len(vals) - 2 * k > 0 else vals
    return sum(core) / len(core)


def daily_index(vks, trim=0.05):
    """V on a 0-100 scale, or None when there are no valid keywords."""
    m = trimmed_mean(vks, trim)
    return None if m is None else round(100 * m, 3)


def percentile(values, p):
    """Linear-interpolated percentile (p in 0-100)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * p / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


SCORE_ANCHORS = (("p50", 5.0), ("p80", 7.0), ("p95", 9.0), ("p99", 10.0))


def score_floor(ref):
    """V that scores 0: as far below the median as P99 is above it (never below 0)."""
    return max(0.0, 2 * ref["p50"] - ref["p99"])


def display_score(v, ref):
    """0-10 score of V against a reference {p50, p80, p95, p99}."""
    if v is None or not ref or not ref.get("p99"):
        return None
    pts = [(score_floor(ref), 0.0)] + [(ref[k], s) for k, s in SCORE_ANCHORS]
    if v <= pts[0][0]:
        return 0.0
    for (x0, s0), (x1, s1) in zip(pts, pts[1:]):
        if v < x1:
            return round(s0 + (s1 - s0) * (v - x0) / (x1 - x0), 1) if x1 > x0 else s0
    return 10.0


def band(v, ref):
    """Calm < P50 <= Normal < P80 <= Elevated < P95 <= Storm."""
    if v is None or not ref:
        return None
    if v < ref["p50"]:
        return "calm"
    if v < ref["p80"]:
        return "normal"
    if v < ref["p95"]:
        return "elevated"
    return "storm"
