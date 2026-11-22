"""Poisoned-SERP guard.

A first fetch is SUSPECT when it does not look like this keyword's recent verified
history, unless it simply continues the last verified day:

  seen          = hosts present on any of the last 7 verified days, or in any fetch of the
                  last 7 calendar days (flagged ones included)
  novelty       = share of today's hosts that are not in `seen`
  suspect if      (novelty >= 0.7  OR  >= 3 social/forum results while the baseline has <= 1)
            and   NOT continuity (Jaccard(URLs today, URLs last verified day) >= 0.7)

Why novelty and not overlap with a "regulars" set: mobile gambling SERPs churn so much that
about 40% of ordinary fetches share less than half of the hosts seen on 3+ of the last 7 days,
so an overlap rule flags normal days. A takeover by hosts never seen in a week is the pattern
that matters. Measured on a 7-week pre-launch sample (7 markets, ~16k keyword-days), novelty
>= 0.7 flags about 6% of fetches.

Flagged fetches count as "seen": a takeover that lasts one day is caught, but one still there
the next day is a real change and is scored, instead of locking the keyword out for good.

Suspect keywords are re-fetched once, at least an hour later:
  second fetch not suspect          -> RECOVERED (second fetch used)
  both suspect and they agree       -> QUARANTINED (confirmed next day, else discarded)
  both suspect and they disagree    -> DISCARDED

Detections are described as "suspected": the guard infers poisoning, it does not prove it.
"""
from statistics import median

from .canonical import domain_of, is_social_or_forum

REFERENCE_DAYS = 7
MIN_HISTORY = 3
NOVELTY_MAX = 0.7
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


def assess(urls, verified_history, recent_fetches=None):
    """Judge one fetch. `verified_history`: URL lists of past verified days, oldest first.
    `recent_fetches`: URL lists of every fetch of the last 7 days, whatever their status.

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

    seen = {d for day in hist + list(recent_fetches or []) for d in domains(day)}
    today = set(domains(urls))
    novelty = len(today - seen) / len(today)
    social_today = social_count(urls)
    social_base = median(social_count(day) for day in hist)
    continuity = jaccard(urls, hist[-1])

    reasons = []
    if novelty >= NOVELTY_MAX:
        reasons.append("%.0f%% of hosts unseen in %d days" % (100 * novelty, len(hist)))
    if social_today >= SOCIAL_SPIKE and social_base <= SOCIAL_BASELINE_MAX:
        reasons.append("%d social/forum results vs baseline %g" % (social_today, social_base))
    info.update(novelty=round(novelty, 3), social=social_today, continuity=round(continuity, 3))
    if reasons and continuity >= AGREE_JACCARD:
        info["reason"] = "continuity override (%s)" % "; ".join(reasons)
        return False, info
    info["reason"] = "; ".join(reasons) if reasons else "ok"
    return bool(reasons), info


def resolve_refetch(first_urls, second_urls, verified_history, recent_fetches=None):
    """Status and URLs to keep after a re-fetch of a suspect keyword.

    `recent_fetches` must not include today's first fetch, or a repeat of it would pass.
    """
    second_suspect, info = assess(second_urls, verified_history, recent_fetches)
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
