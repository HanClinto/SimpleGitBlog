"""
Stage 07 — Fetch HN Favorites.

Reads:  _cache/hn.json   (for effective HN username)
Writes: _cache/hn_favorites.json

Environment variables consumed:
  HN_USERNAME   optional  explicit HN username (falls back to hn cache)
"""

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from blog.ingestors import hackernews, hackernews_favorites  # noqa: E402
from blog.pipeline_cache import read_cache, write_cache, emit_gha_warnings  # noqa: E402


def main() -> None:
    env_hn = os.environ.get("HN_USERNAME") or None

    # Resolve username — prefer explicit env var, then fall back to hn cache
    hn_cache = read_cache("hn")
    effective_usernames: list[str] = (
        hackernews.load_usernames(env_hn)
        or hn_cache.get("effective_hn_usernames", [])
    )

    if not effective_usernames:
        print("HN_USERNAME not configured — skipping HN Favorites ingestor.")
        write_cache("hn_favorites", {
            "stage": "hn_favorites",
            "posts": [],
            "warnings": [],
            "elapsed": 0.0,
            "skipped": True,
        })
        return

    username = effective_usernames[0]
    print(f"Fetching HN Favorites for: {username}…")
    start = time.monotonic()
    posts, warnings = hackernews_favorites.ingest(username)
    elapsed = time.monotonic() - start

    print(f"  {len(posts)} favorite(s) ingested.")
    emit_gha_warnings(warnings)

    write_cache("hn_favorites", {
        "stage": "hn_favorites",
        "posts": posts,
        "warnings": warnings,
        "elapsed": elapsed,
        "skipped": False,
    })
    print(f"  Stage complete in {elapsed:.1f}s ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    main()
