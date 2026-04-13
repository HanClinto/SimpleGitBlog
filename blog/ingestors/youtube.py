"""
YouTube playlist ingestor for SimpleGitBlog.

Fetches videos from one or more YouTube playlists and converts them into the
common post schema.

Section: "watching" (My Watching)

Configuration (GitHub Actions repository settings — do NOT hardcode values):
  Secret:   YOUTUBE_API_KEY          YouTube Data API v3 key
  Variable: YOUTUBE_PLAYLIST_IDS     Comma-separated playlist IDs

For local development, you may also place playlist IDs in
``config/youtube_playlists.txt`` (one per line, # comments supported).
That file is gitignored so your personal IDs stay off of version control.
"""

from pathlib import Path

import requests

from blog.utils import extract_excerpt, format_date, format_datetime, plain_text_to_html

_YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
_CONFIG_FILE = "youtube_playlists.txt"


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_playlist_ids(config_dir: Path, env_playlist_ids: str | None = None) -> list[str]:
    """
    Return a deduplicated list of YouTube playlist IDs from two sources:

    1. ``env_playlist_ids`` — the value of the YOUTUBE_PLAYLIST_IDS env var
       (comma-separated). This is the recommended approach for deployed sites.
    2. ``config/youtube_playlists.txt`` — optional local-dev override file
       (gitignored; never committed with real IDs).
    """
    ids: set[str] = set()

    if env_playlist_ids:
        for pid in env_playlist_ids.split(","):
            pid = pid.strip()
            if pid:
                ids.add(pid)

    config_file = config_dir / _CONFIG_FILE
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.add(line)

    return sorted(ids)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _fetch_playlist_items(api_key: str, playlist_id: str) -> list[dict]:
    """Fetch all items from a YouTube playlist, handling nextPageToken pagination."""
    items: list[dict] = []
    params: dict = {
        "part": "snippet",
        "playlistId": playlist_id,
        "maxResults": 50,
        "key": api_key,
    }
    url = f"{_YOUTUBE_API_BASE}/playlistItems"

    while url:
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
        except requests.HTTPError as exc:
            print(f"  Warning: YouTube API error for playlist {playlist_id}: {exc}")
            break

        data = response.json()
        items.extend(data.get("items", []))

        next_token = data.get("nextPageToken")
        if next_token:
            params = {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": 50,
                "key": api_key,
                "pageToken": next_token,
            }
        else:
            url = None  # no more pages

    return items


# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------

def _process_item(item: dict, playlist_id: str) -> dict | None:
    """Convert a YouTube playlist item snippet into the common post schema."""
    snippet = item.get("snippet", {})
    resource = snippet.get("resourceId", {})
    video_id = resource.get("videoId")
    if not video_id:
        return None  # skip non-video items (e.g. private/deleted)

    title = snippet.get("title") or "Untitled Video"
    description = snippet.get("description") or ""
    published_at = snippet.get("publishedAt") or ""
    channel_title = snippet.get("channelTitle") or "YouTube"

    thumbnails = snippet.get("thumbnails", {})
    thumbnail_url = (
        thumbnails.get("medium", {}).get("url")
        or thumbnails.get("default", {}).get("url")
        or None
    )

    post_id = f"yt-{video_id}"
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    return {
        "post_id": post_id,
        "title": title,
        "post_url": f"/posts/{post_id}/",
        "source_url": video_url,
        "author": channel_title,
        "author_url": f"https://www.youtube.com/watch?v={video_id}",
        "avatar_url": thumbnail_url,
        "created_at": published_at,
        "created_at_fmt": format_date(published_at),
        "created_at_iso": format_datetime(published_at),
        "body_html": plain_text_to_html(description),
        "excerpt": extract_excerpt(description),
        "source": "youtube",
        "section": "watching",
        "labels": [],
        "comment_count": 0,
        "comments": [],
        "metadata": {
            "video_id": video_id,
            "playlist_id": playlist_id,
            "channel_title": channel_title,
            "thumbnail_url": thumbnail_url,
        },
    }


# ---------------------------------------------------------------------------
# Public ingest entry point
# ---------------------------------------------------------------------------

def ingest(
    api_key: str,
    config_dir: Path,
    env_playlist_ids: str | None = None,
) -> list[dict]:
    """
    Fetch YouTube playlist videos and return posts in the common schema.

    Playlist IDs come from ``env_playlist_ids`` (YOUTUBE_PLAYLIST_IDS env var)
    and/or the local ``config/youtube_playlists.txt`` file.
    """
    playlist_ids = load_playlist_ids(config_dir, env_playlist_ids)
    if not playlist_ids:
        print("  No YouTube playlist IDs configured.")
        return []

    posts: list[dict] = []
    seen_video_ids: set[str] = set()

    for playlist_id in playlist_ids:
        print(f"  Fetching playlist: {playlist_id}")
        items = _fetch_playlist_items(api_key, playlist_id)
        print(f"    {len(items)} video(s) found.")
        for item in items:
            post = _process_item(item, playlist_id)
            if post is None:
                continue
            vid = post["metadata"]["video_id"]
            if vid not in seen_video_ids:
                seen_video_ids.add(vid)
                posts.append(post)

    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts
