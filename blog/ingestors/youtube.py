"""
YouTube ingestor for SimpleGitBlog.

Fetches videos from one or more YouTube playlists AND/OR YouTube channels via
YouTube's public Atom/RSS feeds — **no API key required**.

Feed URLs:
    Playlist: https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}
    Channel:  https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}

Each feed returns up to the 15 most-recently-added videos.

Section: "watching" (My Watching)

Configuration (GitHub Actions repository variables — Settings → Variables):
  YOUTUBE_PLAYLIST_IDS     One playlist ID per line (multi-line variable)
  YOUTUBE_CHANNEL_IDS      One channel ID or @handle per line
                           (UCxxxxxx or @username) — handles are resolved
                           automatically.
"""

import re
import xml.etree.ElementTree as ET

import requests

from blog.utils import extract_excerpt, format_date, format_datetime, plain_text_to_html

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

def load_playlist_ids(env_playlist_ids: str | None = None) -> list[str]:
    """
    Return a deduplicated list of YouTube playlist IDs from the
    YOUTUBE_PLAYLIST_IDS repository variable (one ID per line).
    """
    ids: set[str] = set()

    if env_playlist_ids:
        for pid in re.split(r'[\r\n,]+', env_playlist_ids):
            pid = pid.strip()
            if pid:
                ids.add(pid)

    return sorted(ids)


def load_channel_ids(env_channel_ids: str | None = None) -> list[str]:
    """
    Return a deduplicated list of YouTube channel IDs (or @handles) from the
    YOUTUBE_CHANNEL_IDS repository variable (one ID or handle per line).
    """
    ids: set[str] = set()

    if env_channel_ids:
        for cid in re.split(r'[\r\n,]+', env_channel_ids):
            cid = cid.strip()
            if cid:
                ids.add(cid)

    return sorted(ids)


def _resolve_channel_id(handle_or_id: str, warnings: list[str] | None = None) -> str | None:
    """
    Resolve a channel handle (``@username``) or channel ID (``UCxxxxxx``) to a
    confirmed channel ID.

    If the argument looks like a channel ID already (starts with ``UC``), it is
    returned as-is.  Otherwise the channel page is fetched and the RSS feed link
    (which contains the channel ID) is extracted from the HTML.

    Returns the channel ID string, or ``None`` if resolution fails.
    """
    # Already looks like a channel ID
    if re.match(r'^UC[A-Za-z0-9_-]{22}$', handle_or_id):
        return handle_or_id

    # Build the canonical channel URL
    if handle_or_id.startswith("@"):
        channel_url = f"https://www.youtube.com/{handle_or_id}"
    else:
        channel_url = f"https://www.youtube.com/@{handle_or_id}"

    try:
        resp = requests.get(
            channel_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SimpleGitBlog/1.0; +https://github.com)"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        msg = f"Warning: could not fetch channel page for {handle_or_id}: {exc}"
        print(f"  {msg}")
        if warnings is not None:
            warnings.append(msg)
        return None

    # Look for the RSS feed link in the page HTML, which contains the channel_id
    m = re.search(
        r'https://www\.youtube\.com/feeds/videos\.xml\?channel_id=(UC[A-Za-z0-9_-]+)',
        resp.text,
    )
    if m:
        return m.group(1)

    msg = f"Warning: could not extract channel ID from {channel_url}"
    print(f"  {msg}")
    if warnings is not None:
        warnings.append(msg)
    return None


# ---------------------------------------------------------------------------
# RSS / Atom feed helpers
# ---------------------------------------------------------------------------

def _fetch_feed(url: str, label: str, warnings: list[str] | None = None) -> list[dict]:
    """
    Fetch videos from a YouTube Atom feed (playlist or channel).

    Returns a list of raw entry dicts extracted from the feed.
    No API key required — the feed is publicly accessible.
    """
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.HTTPError as exc:
        msg = f"Warning: YouTube RSS error for {label}: {exc}"
        print(f"  {msg}")
        if warnings is not None:
            warnings.append(msg)
        return []
    except requests.RequestException as exc:
        msg = f"Warning: YouTube RSS request failed for {label}: {exc}"
        print(f"  {msg}")
        if warnings is not None:
            warnings.append(msg)
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        msg = f"Warning: could not parse YouTube RSS for {label}: {exc}"
        print(f"  {msg}")
        if warnings is not None:
            warnings.append(msg)
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


def _fetch_playlist_feed(playlist_id: str, warnings: list[str] | None = None) -> list[dict]:
    """Fetch videos from a YouTube playlist Atom feed."""
    url = f"{_RSS_BASE}?playlist_id={playlist_id}"
    return _fetch_feed(url, f"playlist {playlist_id}", warnings=warnings)


def _fetch_channel_feed(channel_id: str, warnings: list[str] | None = None) -> list[dict]:
    """Fetch latest videos from a YouTube channel Atom feed."""
    url = f"{_RSS_BASE}?channel_id={channel_id}"
    return _fetch_feed(url, f"channel {channel_id}", warnings=warnings)


# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------

def _process_entry(entry: dict, source_type: str, source_id: str, view_more_url: str) -> dict | None:
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
        "reactions":      [],
        "comment_count":  0,
        "comments":       [],
        "metadata": {
            "video_id":      video_id,
            "source_type":   source_type,   # "playlist" or "channel"
            "source_id":     source_id,
            "view_more_url": view_more_url,
            "channel_name":  author_name,
            "thumbnail_url": thumbnail_url,
            # Legacy aliases kept for template compatibility
            "playlist_id":   source_id if source_type == "playlist" else None,
        },
    }


# ---------------------------------------------------------------------------
# Public ingest entry point
# ---------------------------------------------------------------------------

def ingest(
    env_playlist_ids: str | None = None,
    env_channel_ids: str | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Fetch YouTube playlist and channel videos via public RSS feeds and return
    ``(posts, warnings)`` where *warnings* is a list of human-readable problem
    descriptions (e.g. HTTP 404 on an RSS feed).  No API key required.

    Playlist IDs come from the YOUTUBE_PLAYLIST_IDS repository variable.
    Channel IDs/handles come from the YOUTUBE_CHANNEL_IDS repository variable.
    """
    warnings: list[str] = []
    playlist_ids = load_playlist_ids(env_playlist_ids)
    channel_ids_raw = load_channel_ids(env_channel_ids)

    if not playlist_ids and not channel_ids_raw:
        print("  No YouTube playlist IDs or channel IDs configured.")
        return [], warnings

    posts: list[dict] = []
    seen_video_ids: set[str] = set()

    for playlist_id in playlist_ids:
        print(f"  Fetching playlist RSS: {playlist_id}")
        entries = _fetch_playlist_feed(playlist_id, warnings=warnings)
        print(f"    {len(entries)} video(s) found.")
        view_more_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        for entry in entries:
            post = _process_entry(entry, "playlist", playlist_id, view_more_url)
            if post is None:
                continue
            vid = post["metadata"]["video_id"]
            if vid not in seen_video_ids:
                seen_video_ids.add(vid)
                posts.append(post)

    for raw_id in channel_ids_raw:
        print(f"  Resolving YouTube channel: {raw_id}")
        channel_id = _resolve_channel_id(raw_id, warnings=warnings)
        if not channel_id:
            print(f"    Skipping — could not resolve channel ID for: {raw_id}")
            continue
        print(f"  Fetching channel RSS: {channel_id}")
        entries = _fetch_channel_feed(channel_id, warnings=warnings)
        print(f"    {len(entries)} video(s) found.")
        # Build a human-friendly "view more" URL using the original handle if given
        if raw_id.startswith("@"):
            view_more_url = f"https://www.youtube.com/{raw_id}/videos"
        else:
            view_more_url = f"https://www.youtube.com/channel/{channel_id}/videos"
        for entry in entries:
            post = _process_entry(entry, "channel", channel_id, view_more_url)
            if post is None:
                continue
            vid = post["metadata"]["video_id"]
            if vid not in seen_video_ids:
                seen_video_ids.add(vid)
                posts.append(post)

    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts, warnings
