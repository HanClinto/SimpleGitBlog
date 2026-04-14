"""
Stage 03 — Fetch YouTube content (My Watching / My Videos).

Reads:  _cache/profile.json   (for auto-discovered channel handles)
Writes: _cache/youtube.json

Environment variables consumed:
  YOUTUBE_PLAYLIST_IDS  optional  one playlist ID per line
  YOUTUBE_CHANNEL_IDS   optional  explicit channel IDs/handles (overrides auto-discovery)
"""

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from blog.ingestors import youtube  # noqa: E402
from blog.pipeline_cache import read_cache, write_cache, emit_gha_warnings  # noqa: E402


def main() -> None:
    env_playlist_ids = os.environ.get("YOUTUBE_PLAYLIST_IDS") or None
    env_channel_ids = os.environ.get("YOUTUBE_CHANNEL_IDS") or None

    # Read the effective channel IDs resolved during stage 01 (auto-discovery).
    # Fall back gracefully if the profile cache is absent.
    profile_cache = read_cache("profile")
    effective_channel_ids_str = (
        env_channel_ids
        or profile_cache.get("effective_youtube_channel_ids_str")
    )
    auto_discovered_channels: list[str] = profile_cache.get("auto_discovered_channels", [])

    playlist_ids = youtube.load_playlist_ids(env_playlist_ids)
    channel_ids = youtube.load_channel_ids(effective_channel_ids_str)

    if not playlist_ids and not channel_ids:
        print(
            "YOUTUBE_PLAYLIST_IDS / YOUTUBE_CHANNEL_IDS not configured"
            " and none found in GitHub profile — skipping YouTube ingestor."
        )
        write_cache("youtube", {
            "stage": "youtube",
            "posts": [],
            "playlist_ids": [],
            "channel_ids": [],
            "auto_discovered_channels": auto_discovered_channels,
            "warnings": [],
            "elapsed": 0.0,
            "skipped": True,
        })
        return

    print("Fetching YouTube content (My Watching)…")
    start = time.monotonic()
    posts, warnings = youtube.ingest(env_playlist_ids, effective_channel_ids_str)
    elapsed = time.monotonic() - start

    print(f"  {len(posts)} post(s) ingested from YouTube.")
    emit_gha_warnings(warnings)

    write_cache("youtube", {
        "stage": "youtube",
        "posts": posts,
        "playlist_ids": playlist_ids,
        "channel_ids": channel_ids,
        "auto_discovered_channels": auto_discovered_channels,
        "warnings": warnings,
        "elapsed": elapsed,
        "skipped": False,
    })
    print(f"  Stage complete in {elapsed:.1f}s ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    main()
