"""Poisoned-SERP guard.

A first fetch is SUSPECT when it does not look like this keyword's recent verified
history, unless it simply continues the last verified day:

  reference R   = domains present in >= 3 of the last 7 verified days
  overlap       = |domains(today) & R| / |R|
  suspect if      (overlap < 0.5  OR  >= 3 social/forum results while the baseline has <= 1)
            and   NOT continuity (Jaccard(URLs today, URLs last verified day) >= 0.7)

Suspect keywords are re-fetched once, at least an hour later:
  second fetch not suspect          -> RECOVERED (second fetch used)
  both suspect and they agree       -> QUARANTINED (confirmed next day, else discarded)
  both suspect and they disagree    -> DISCARDED

Detections are described as "suspected": the guard infers poisoning, it does not prove it.
"""
from collections import Counter
from statistics import median

from .canonical import domain_of, is_social_or_forum

REFERENCE_DAYS = 7
REFERENCE_MIN_OCCURRENCE = 3
MIN_HISTORY = 3
OVERLAP_MIN = 0.5
SOCIAL_SPIKE = 3
SOCIAL_BASELINE_MAX = 1
AGREE_JACCARD = 0.7

VERIFIED = ("clean", "recovered", "confirmed_shift")


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def domains(urls):
    return [domain_of(u) for u in urls]


def social_count(urls):
    return sum(1 for d in domains(urls) if is_social_or_forum(d))


def assess(urls, verified_history):
    """Judge one fetch. `verified_history`: URL lists of past verified days, oldest first.

    Returns (suspect, info) where info explains the decision.
    """
    hist = [h for h in verified_history if h][-REFERENCE_DAYS:]
    info = {"history_days": len(hist)}
    if not urls:
        info["reason"] = "empty result"
        return True, info
    if len(hist) < MIN_HISTORY:
        info["reason"] = "not enough history to judge"
        return False, info

    counts = Counter(d for day in hist for d in set(domains(day)))
    reference = {d for d, c in counts.items() if c >= REFERENCE_MIN_OCCURRENCE}
    today = set(domains(urls))
    overlap = len(today & reference) / len(reference) if reference else 1.0
    social_today = social_count(urls)
    social_base = median(social_count(day) for day in hist)
    continuity = jaccard(urls, hist[-1])

    reasons = []
    if overlap < OVERLAP_MIN:
        reasons.append("overlap %.2f < %.2f" % (overlap, OVERLAP_MIN))
    if social_today >= SOCIAL_SPIKE and social_base <= SOCIAL_BASELINE_MAX:
        reasons.append("%d social/forum results vs baseline %g" % (social_today, social_base))
    info.update(overlap=round(overlap, 3), social=social_today, continuity=round(continuity, 3))
    if reasons and continuity >= AGREE_JACCARD:
        info["reason"] = "continuity override (%s)" % "; ".join(reasons)
        return False, info
    info["reason"] = "; ".join(reasons) if reasons else "ok"
    return bool(reasons), info


def resolve_refetch(first_urls, second_urls, verified_history):
    """Status and URLs to keep after a re-fetch of a suspect keyword."""
    second_suspect, info = assess(second_urls, verified_history)
    if not first_urls and not second_urls:
        # Two empty pages are a fetch problem, not a SERP shift to confirm tomorrow.
        info["reason"] = "empty on both fetches"
        return "discarded", None, info
    if not second_suspect:
        return "recovered", second_urls, info
    if jaccard(first_urls, second_urls) >= AGREE_JACCARD:
        return "quarantined", second_urls, info
    return "discarded", None, info


def confirm_quarantine(quarantined_urls, next_day_urls):
    """A quarantined day is a real shift when the next day shows the same SERP."""
    return bool(next_day_urls) and jaccard(quarantined_urls, next_day_urls) >= AGREE_JACCARD
