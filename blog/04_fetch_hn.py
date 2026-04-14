"""
Stage 04 — Fetch Hacker News content (My Reading).

Reads:  _cache/profile.json   (for auto-discovered HN username)
Writes: _cache/hn.json

Environment variables consumed:
  HN_USERNAME   optional  explicit HN username (overrides auto-discovery)
"""

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from blog.ingestors import hackernews  # noqa: E402
from blog.pipeline_cache import read_cache, write_cache, emit_gha_warnings  # noqa: E402


def main() -> None:
    env_hn = os.environ.get("HN_USERNAME") or None

    # Read effective HN usernames resolved during stage 01 (auto-discovery).
    # Fall back gracefully if the profile cache is absent.
    profile_cache = read_cache("profile")
    effective_hn_usernames: list[str] = (
        hackernews.load_usernames(env_hn)
        or profile_cache.get("effective_hn_usernames", [])
    )
    auto_discovered_hn_username: str | None = profile_cache.get("auto_discovered_hn_username")

    if not effective_hn_usernames:
        print(
            "HN_USERNAME not configured and none found in GitHub profile"
            " — skipping Hacker News ingestor."
        )
        write_cache("hn", {
            "stage": "hn",
            "posts": [],
            "effective_hn_usernames": [],
            "auto_discovered_hn_username": None,
            "warnings": [],
            "elapsed": 0.0,
            "skipped": True,
        })
        return

    names_str = ", ".join(effective_hn_usernames)
    print(f"Fetching Hacker News (My Reading) for: {names_str}…")
    start = time.monotonic()
    posts, warnings = hackernews.ingest(effective_hn_usernames)
    elapsed = time.monotonic() - start

    print(f"  {len(posts)} post(s) ingested from Hacker News.")
    emit_gha_warnings(warnings)

    write_cache("hn", {
        "stage": "hn",
        "posts": posts,
        "effective_hn_usernames": effective_hn_usernames,
        "auto_discovered_hn_username": auto_discovered_hn_username,
        "warnings": warnings,
        "elapsed": elapsed,
        "skipped": False,
    })
    print(f"  Stage complete in {elapsed:.1f}s ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    main()
