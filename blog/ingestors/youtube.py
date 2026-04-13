"""
YouTube playlist ingestor for SimpleGitBlog.

Fetches videos from one or more YouTube playlists via YouTube's public Atom/RSS
feed — **no API key required**.

Feed URL:
    https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}

Each playlist returns up to the 15 most-recently-added videos. For a personal
"My Watching" section this is normally plenty; add multiple playlists (e.g. one
per year) if you need more history.

Section: "watching" (My Watching)

Configuration (GitHub Actions repository settings — do NOT hardcode values):
  Variable: YOUTUBE_PLAYLIST_IDS     Comma-separated playlist IDs

For local development, you may also place playlist IDs in
``config/youtube_playlists.txt`` (one per line, # comments supported).
That file is gitignored so your personal IDs stay off of version control.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from blog.utils import extract_excerpt, format_date, format_datetime, plain_text_to_html

_CONFIG_FILE = "youtube_playlists.txt"
_RSS_BASE = "https://www.youtube.com/feeds/videos.xml"

# XML namespace map for YouTube Atom feeds
_NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "yt":    "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


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
# RSS / Atom feed helpers
# ---------------------------------------------------------------------------

def _fetch_playlist_feed(playlist_id: str) -> list[dict]:
    """
    Fetch videos from a YouTube playlist Atom feed.

    Returns a list of raw entry dicts extracted from the feed.
    No API key required — the feed is publicly accessible.
    """
    url = f"{_RSS_BASE}?playlist_id={playlist_id}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.HTTPError as exc:
        print(f"  Warning: YouTube RSS error for playlist {playlist_id}: {exc}")
        return []
    except requests.RequestException as exc:
        print(f"  Warning: YouTube RSS request failed for playlist {playlist_id}: {exc}")
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        print(f"  Warning: could not parse YouTube RSS for playlist {playlist_id}: {exc}")
        return []

    entries = []
    for entry in root.findall("atom:entry", _NS):
        video_id = entry.findtext("yt:videoId", namespaces=_NS) or ""
        title    = entry.findtext("atom:title",     namespaces=_NS) or "Untitled Video"
        published = entry.findtext("atom:published", namespaces=_NS) or ""

        author_el = entry.find("atom:author", _NS)
        author_name = (
            author_el.findtext("atom:name", namespaces=_NS)
            if author_el is not None else "YouTube"
        ) or "YouTube"
        author_uri = (
            author_el.findtext("atom:uri", namespaces=_NS)
            if author_el is not None else ""
        ) or ""

        media_group = entry.find("media:group", _NS)
        description = ""
        thumbnail_url = None
        if media_group is not None:
            description = media_group.findtext("media:description", namespaces=_NS) or ""
            thumb_el = media_group.find("media:thumbnail", _NS)
            if thumb_el is not None:
                thumbnail_url = thumb_el.get("url") or None

        entries.append({
            "video_id":     video_id,
            "title":        title,
            "published":    published,
            "author_name":  author_name,
            "author_uri":   author_uri,
            "description":  description,
            "thumbnail_url": thumbnail_url,
        })

    return entries


# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------

def _process_entry(entry: dict, playlist_id: str) -> dict | None:
    """Convert a raw feed entry dict into the common post schema."""
    video_id = entry.get("video_id", "").strip()
    if not video_id:
        return None  # deleted / private

    title         = entry.get("title") or "Untitled Video"
    published     = entry.get("published") or ""
    description   = entry.get("description") or ""
    author_name   = entry.get("author_name") or "YouTube"
    author_uri    = entry.get("author_uri") or f"https://www.youtube.com/watch?v={video_id}"
    thumbnail_url = entry.get("thumbnail_url")

    post_id   = f"yt-{video_id}"
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    return {
        "post_id":        post_id,
        "title":          title,
        "post_url":       f"/posts/{post_id}/",
        "source_url":     video_url,
        "author":         author_name,
        "author_url":     author_uri,
        "avatar_url":     thumbnail_url,
        "created_at":     published,
        "created_at_fmt": format_date(published),
        "created_at_iso": format_datetime(published),
        "body_html":      plain_text_to_html(description),
        "excerpt":        extract_excerpt(description),
        "source":         "youtube",
        "section":        "watching",
        "labels":         [],
        "comment_count":  0,
        "comments":       [],
        "metadata": {
            "video_id":      video_id,
            "playlist_id":   playlist_id,
            "channel_name":  author_name,
            "thumbnail_url": thumbnail_url,
        },
    }


# ---------------------------------------------------------------------------
# Public ingest entry point
# ---------------------------------------------------------------------------

def ingest(
    config_dir: Path,
    env_playlist_ids: str | None = None,
) -> list[dict]:
    """
    Fetch YouTube playlist videos via public RSS feeds and return posts in
    the common schema.  No API key required.

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
        print(f"  Fetching playlist RSS: {playlist_id}")
        entries = _fetch_playlist_feed(playlist_id)
        print(f"    {len(entries)} video(s) found.")
        for entry in entries:
            post = _process_entry(entry, playlist_id)
            if post is None:
                continue
            vid = post["metadata"]["video_id"]
            if vid not in seen_video_ids:
                seen_video_ids.add(vid)
                posts.append(post)

    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts
