"""URL canonicalisation and domain helpers."""
from urllib.parse import parse_qsl, urlencode, urlsplit

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {
    "gclid", "fbclid", "msclkid", "yclid", "srsltid", "igshid", "mc_cid", "mc_eid",
    "_ga", "_gl", "ref", "ref_src", "affid", "aff_id", "clickid", "btag",
}
SOCIAL_FORUM_DOMAINS = ("facebook.com", "instagram.com", "linkedin.com", "quora.com", "reddit.com")


def canonical_url(url):
    """'https://www.Example.com/Path/?utm_source=x&b=2&a=1#top' -> 'example.com/Path?a=1&b=2'.

    Host lowercased, scheme/www/fragment/trailing slash and tracking or affiliate
    parameters removed, remaining query parameters sorted. Path case is kept.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    path = (parts.path or "").rstrip("/")
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in TRACKING_KEYS
    ]
    query.sort()
    out = host + path
    if query:
        out += "?" + urlencode(query)
    return out


def domain_of(canonical):
    """Host part of a canonical URL or a host identity ("example.com#2" -> "example.com")."""
    return canonical.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]


def identities(raw_urls, level="host"):
    """Ordered result identities used for scoring.

    level "host" (default): the site's host name; a host's 2nd and later results in the same
    top 10 become "host#2", "host#3" so every position keeps its own identity.
    level "url": the full canonical URL, de-duplicated.
    """
    out, seen = [], {}
    for u in raw_urls or []:
        c = canonical_url(u)
        if not c:
            continue
        key = domain_of(c) if level == "host" else c
        n = seen.get(key, 0) + 1
        seen[key] = n
        if n == 1:
            out.append(key)
        elif level == "host":
            out.append("%s#%d" % (key, n))
    return out


def is_social_or_forum(domain):
    return any(domain == d or domain.endswith("." + d) for d in SOCIAL_FORUM_DOMAINS)
