"""Downloaden met cache. Alles wat de wereldbouwer ophaalt komt in worlds/<naam>/cache/, zodat opnieuw bouwen
offline kan en een wereld na één keer downloaden zonder internet werkt."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

USER_AGENT = "openagbot-fieldsim/0.1 (Bruut OpenAgbot simulator)"


def fetch(url: str, cache_dir: str | None = None, timeout: float = 60.0, retries: int = 3) -> bytes:
    path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, hashlib.sha1(url.encode("utf-8")).hexdigest()[:20])
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if path:
                with open(path, "wb") as fh:
                    fh.write(data)
                with open(path + ".url", "w", encoding="utf-8") as fh:
                    fh.write(url)
            return data
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"downloaden mislukt: {url}: {last}")


def fetch_json(url: str, cache_dir: str | None = None) -> dict:
    return json.loads(fetch(url, cache_dir).decode("utf-8"))


def fetch_features(url: str, cache_dir: str | None = None, max_pages: int = 50) -> list[dict]:
    """OGC API Features: alle pagina's volgen (rel=next)."""
    out = []
    for _ in range(max_pages):
        page = fetch_json(url, cache_dir)
        out += page.get("features", [])
        nxt = [link["href"] for link in page.get("links", []) if link.get("rel") == "next"]
        if not nxt:
            break
        url = nxt[0]
    return out
