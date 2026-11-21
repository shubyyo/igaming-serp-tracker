"""Minimal DataForSEO SERP API client (Google Organic, Standard queue). Standard library only.

Credentials come from the environment: DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.
DATAFORSEO_BASE switches host (default https://api.dataforseo.com, sandbox
https://sandbox.dataforseo.com for free dummy data while testing).
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

ORGANIC = "/v3/serp/google/organic"
MAX_TASKS_PER_POST = 100


class DataForSEOError(RuntimeError):
    pass


class Client:
    def __init__(self, login=None, password=None, base=None, timeout=60):
        login = login or os.environ.get("DATAFORSEO_LOGIN")
        password = password or os.environ.get("DATAFORSEO_PASSWORD")
        if not login or not password:
            raise DataForSEOError("Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD")
        token = base64.b64encode(("%s:%s" % (login, password)).encode()).decode()
        self.headers = {"Authorization": "Basic " + token, "Content-Type": "application/json"}
        self.base = (base or os.environ.get("DATAFORSEO_BASE") or "https://api.dataforseo.com").rstrip("/")
        self.timeout = timeout

    def _call(self, method, path, body=None, retries=3):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries):
            req = urllib.request.Request(self.base + path, data=data, headers=self.headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode())
                break
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == retries - 1:
                    raise DataForSEOError("%s %s failed: %s" % (method, path, e))
                time.sleep(5 * (attempt + 1))
        if payload.get("status_code") != 20000:
            raise DataForSEOError("%s %s: %s %s" % (method, path, payload.get("status_code"), payload.get("status_message")))
        return payload

    def post_tasks(self, tasks):
        """Post task dicts in batches of 100. Returns [(task_id, tag)] for created tasks, plus errors."""
        created, errors = [], []
        for i in range(0, len(tasks), MAX_TASKS_PER_POST):
            payload = self._call("POST", ORGANIC + "/task_post", tasks[i:i + MAX_TASKS_PER_POST])
            for t in payload.get("tasks", []):
                tag = (t.get("data") or {}).get("tag")
                if t.get("status_code") == 20100:
                    created.append((t["id"], tag))
                else:
                    errors.append((tag, t.get("status_code"), t.get("status_message")))
        return created, errors

    def tasks_ready(self):
        """IDs of completed, not yet collected tasks (up to 1000 per call)."""
        payload = self._call("GET", ORGANIC + "/tasks_ready")
        ids = []
        for t in payload.get("tasks", []):
            for r in t.get("result") or []:
                ids.append(r["id"])
        return ids

    def task_get(self, task_id):
        """Raw task_get/regular payload for one task."""
        return self._call("GET", ORGANIC + "/task_get/regular/" + task_id)


def build_task(keyword, market, tag, depth=10, resolve_poison=True):
    return {
        "keyword": keyword,
        "location_code": market["location_code"],
        "language_code": market["language_code"],
        "device": market.get("device", "mobile"),
        "os": market.get("os", "android"),
        "depth": depth,
        "tag": tag,
        "resolve_poisoned_results": bool(resolve_poison),
    }


def parse_organic(payload, depth=10):
    """From a task_get/regular payload: (raw organic URLs in rank order, repaired?, task status)."""
    task = (payload.get("tasks") or [{}])[0]
    status = task.get("status_code")
    results = task.get("result") or []
    if status != 20000 or not results:
        return None, False, status
    items = results[0].get("items") or []
    organic = sorted((it for it in items if it.get("type") == "organic"), key=lambda it: it.get("rank_group") or 999)
    urls = [it.get("url") for it in organic if (it.get("rank_group") or 999) <= depth]
    repaired = any("from_web" in (it.get("checks") or []) for it in organic)
    return urls, repaired, status
