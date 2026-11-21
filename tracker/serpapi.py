"""SerpApi provider (Google Search API). Standard library only.

SerpApi is synchronous: each call returns the SERP, so there is no post/collect split.
The tracker runs the first fetches at `collect` time and the re-fetches at `resolve` time,
which keeps the guard's ~90-minute gap between the two.

Key: SERPAPI_KEY (GitHub secret, or a local .env). Every search uses no_cache=true, because a
cached result (up to 1 hour old) would make the re-fetch a copy of the first fetch.
One search = one credit. The Account API used by `quota` is free.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

BASE = "https://serpapi.com"


class SerpApiError(RuntimeError):
    pass


def _is_google_redirect(url):
    p = urllib.parse.urlsplit(url or "")
    host = (p.hostname or "").lower()
    return host.startswith(("google.", "www.google.")) and p.path in ("/goto", "/url")


def result_url(r):
    """Destination of an organic result. Google often returns google.*/goto?url=<opaque> instead
    of the real link; then the displayed link ("https://www.site.com › path") gives the site."""
    link = r.get("link") or ""
    if link and not _is_google_redirect(link):
        return link
    shown = (r.get("displayed_link") or "").split("›", 1)[0].strip()
    if not shown or " " in shown:
        return None
    return shown if shown.startswith(("http://", "https://")) else "https://" + shown


class Client:
    def __init__(self, api_key=None, timeout=60, workers=6):
        self.key = api_key or os.environ.get("SERPAPI_KEY")
        if not self.key:
            raise SerpApiError("Set SERPAPI_KEY")
        self.timeout = timeout
        self.workers = workers

    def _get(self, path, params, retries=3):
        q = urllib.parse.urlencode(dict(params, api_key=self.key))
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(BASE + path + "?" + q, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                try:
                    msg = json.loads(body).get("error", body)
                except ValueError:
                    msg = body[:200]
                if e.code in (401, 403):
                    raise SerpApiError("SerpApi refused the key (%s): %s" % (e.code, msg))
                if e.code == 429 or e.code >= 500:
                    if attempt < retries - 1:
                        time.sleep(10 * (attempt + 1))
                        continue
                # Never include the request URL in errors: it carries the key.
                raise SerpApiError("SerpApi %s %s: %s" % (path, e.code, msg))
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == retries - 1:
                    raise SerpApiError("SerpApi %s failed: %s" % (path, getattr(e, "reason", e)))
                time.sleep(5 * (attempt + 1))

    def account(self):
        """Plan and remaining searches (free call)."""
        return self._get("/account.json", {})

    def search(self, keyword, market, depth=10):
        """(raw organic URLs in rank order | None on failure, repaired=False)."""
        params = {
            "engine": "google",
            "q": keyword,
            "google_domain": market["google_domain"],
            "gl": market["gl"],
            "hl": market.get("hl", market["language_code"]),
            "device": market.get("device", "mobile"),
            "no_cache": "true",
        }
        try:
            data = self._get("/search.json", params)
        except SerpApiError as e:
            if "refused the key" in str(e):
                raise
            return None, False
        err = data.get("error")
        if err:
            # "Google hasn't returned any results for this query." is a real, empty SERP.
            return ([], False) if "hasn't returned any results" in err else (None, False)
        organic = sorted(data.get("organic_results") or [], key=lambda r: r.get("position") or 999)
        urls = []
        for r in organic:
            if (r.get("position") or 999) > depth:
                continue
            u = result_url(r)
            if u:
                urls.append(u)
        return urls[:depth], False

    def fetch_many(self, items, markets_by_code, depth=10):
        """items: [(market_code, keyword)] -> {market: {keyword: (urls|None, repaired)}}."""
        out = defaultdict(dict)

        def one(item):
            code, kw = item
            return code, kw, self.search(kw, markets_by_code[code], depth)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for code, kw, res in pool.map(one, items):
                out[code][kw] = res
        return out
