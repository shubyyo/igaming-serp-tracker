"""Day files (data/days/YYYY-MM-DD.json.gz) and small state files (data/state/*.json)."""
import gzip
import json
import os
from datetime import date, timedelta

from . import config


def iso(d):
    return d if isinstance(d, str) else d.isoformat()


def shift(d, days):
    return (date.fromisoformat(iso(d)) + timedelta(days=days)).isoformat()


class Store:
    def __init__(self, data_dir=None):
        data_dir = data_dir or config.DATA
        self.days_dir = os.path.join(data_dir, "days")
        self.state_dir = os.path.join(data_dir, "state")
        os.makedirs(self.days_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)
        self._cache = {}

    # days -------------------------------------------------------------
    def _day_path(self, d):
        return os.path.join(self.days_dir, iso(d) + ".json.gz")

    def load_day(self, d, create=False):
        d = iso(d)
        if d in self._cache:
            return self._cache[d]
        path = self._day_path(d)
        if os.path.exists(path):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                day = json.load(f)
        elif create:
            day = {"date": d, "markets": {}, "revisions": []}
        else:
            return None
        self._cache[d] = day
        return day

    def save_day(self, day):
        path = self._day_path(day["date"])
        tmp = path + ".tmp"
        # mtime=0 keeps the gzip bytes stable, so unchanged days make no git diff
        with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(day, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        os.replace(tmp, path)
        self._cache[day["date"]] = day

    def list_days(self):
        return sorted(n[:10] for n in os.listdir(self.days_dir) if n.endswith(".json.gz"))

    def entry(self, d, market, keyword):
        day = self.load_day(d)
        if not day:
            return None
        return day["markets"].get(market, {}).get(keyword)

    # state ------------------------------------------------------------
    def load_state(self, name, default=None):
        path = os.path.join(self.state_dir, name + ".json")
        if not os.path.exists(path):
            return default
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, name, obj):
        path = os.path.join(self.state_dir, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")

    def clear_state(self, name):
        path = os.path.join(self.state_dir, name + ".json")
        if os.path.exists(path):
            os.remove(path)
