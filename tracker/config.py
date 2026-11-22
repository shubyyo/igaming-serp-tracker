"""Paths and configuration."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config")
DATA = os.path.join(ROOT, "data")
DAYS = os.path.join(DATA, "days")
STATE = os.path.join(DATA, "state")
SITE = os.path.join(ROOT, "site")


def load_dotenv(path=None):
    """Read KEY=VALUE lines from a local .env (gitignored) without overriding real env vars."""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_settings():
    with open(os.path.join(CONFIG, "settings.json"), encoding="utf-8") as f:
        s = json.load(f)
    # TRACKER_PROVIDER (a GitHub repository variable) switches provider without a commit.
    s["provider"] = (os.environ.get("TRACKER_PROVIDER") or s.get("provider") or "dataforseo").lower()
    s.setdefault("calibration", load_calibration())
    return s


def load_calibration():
    """Per-series starting references measured on a pre-launch SERP sample (config/calibration.json).

    Used instead of the generic bootstrap until a series has `reference_min_days` of its own history.
    """
    path = os.path.join(CONFIG, "calibration.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        cal = json.load(f)
    return {code: dict(ref, source="calibration", sample=cal.get("sample"))
            for code, ref in cal.get("series", {}).items()}


def tool_dir(site_dir, settings):
    """Where this tool's page, data and badges live inside the site (e.g. site/igaming-serp-volatility)."""
    return os.path.join(site_dir, settings.get("tool_path", ""))


def load_markets():
    """Enabled markets with their frozen keyword baskets."""
    with open(os.path.join(CONFIG, "markets.json"), encoding="utf-8") as f:
        markets = [m for m in json.load(f)["markets"] if m.get("enabled")]
    for m in markets:
        path = os.path.join(CONFIG, "keywords", m["code"] + ".txt")
        kws = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        kws.append(line.lower())
        m["keywords"] = sorted(set(kws))
    return markets


def load_annotations():
    path = os.path.join(CONFIG, "annotations.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs():
    for d in (DAYS, STATE):
        os.makedirs(d, exist_ok=True)
