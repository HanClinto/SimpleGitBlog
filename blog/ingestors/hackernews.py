"""
Hacker News ingestor for SimpleGitBlog.

Fetches story submissions and comments by configured usernames via the
Algolia HN Search API (no authentication required).

Section: "reading" (My Reading)

Configuration (GitHub Actions repository settings — do NOT hardcode values):
  Variable: HN_USERNAME    Your Hacker News username

For local development, you may also place usernames in
``config/hackernews.txt`` (one per line, # comments supported).
That file is gitignored so your personal username stays off of version control.
"""

import re
import urllib.parse
from pathlib import Path

import requests

from blog.utils import extract_excerpt, format_date, format_datetime, sanitize_html

_HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
_HN_ITEM_BASE = "https://news.ycombinator.com/item"
_CONFIG_FILE = "hackernews.txt"
_MAX_HITS_PER_TYPE = 100


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_usernames(config_dir: Path, env_username: str | None = None) -> list[str]:
    """
    Return a deduplicated list of HN usernames from two sources:

    1. ``env_username`` — the value of the HN_USERNAME env var.
       This is the recommended approach for deployed sites.
    2. ``config/hackernews.txt`` — optional local-dev override file
       (gitignored; never committed with real usernames).

    Keeping usernames out of committed files ensures forks of this blog
    don't inadvertently display the original owner's HN activity.
    """
    usernames: set[str] = set()

    if env_username:
        usernames.add(env_username.strip())

    config_file = config_dir / _CONFIG_FILE
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                usernames.add(line)

    return sorted(usernames)


# ---------------------------------------------------------------------------
# Algolia HN API helpers
# ---------------------------------------------------------------------------

def _fetch_hn_items(
    username: str,
    item_type: str,
    max_hits: int = _MAX_HITS_PER_TYPE,
) -> list[dict]:
    """
    Fetch stories or comments for a HN username via the Algolia Search API.

    ``item_type`` must be ``"story"`` or ``"comment"``.
    Results are sorted newest-first (search_by_date).
    """
    hits: list[dict] = []
    page = 0

    while len(hits) < max_hits:
        params = {
            "tags": f"{item_type},author_{username}",
            "hitsPerPage": min(50, max_hits - len(hits)),
            "page": page,
        }
        try:
            response = requests.get(
                f"{_HN_ALGOLIA_BASE}/search_by_date",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"  Warning: HN API error for {username} ({item_type}): {exc}")
            break

        data = response.json()
        page_hits = data.get("hits", [])
        if not page_hits:
            break
        hits.extend(page_hits)

        if page >= data.get("nbPages", 1) - 1:
            break
        page += 1

    return hits


# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------

def _process_story(hit: dict) -> dict | None:
    """Convert an HN story hit into the common post schema."""
    object_id = hit.get("objectID")
    if not object_id:
        return None

    title = hit.get("title") or "Untitled Submission"
    author = hit.get("author") or "unknown"
    created_at = hit.get("created_at") or ""
    story_text = hit.get("story_text") or ""
    points = hit.get("points") or 0
    num_comments = hit.get("num_comments") or 0

    hn_url = f"{_HN_ITEM_BASE}?{urllib.parse.urlencode({'id': object_id})}"
    # For link submissions url points to the external article;
    # for self-posts (Ask HN, etc.) url is None — fall back to the HN thread.
    article_url = hit.get("url") or hn_url

    body_parts: list[str] = []
    if story_text:
        body_parts.append(sanitize_html(story_text))
    body_parts.append(
        f'<p><a href="{hn_url}" rel="nofollow noopener noreferrer">'
        f"View HN discussion ({num_comments} comment{'s' if num_comments != 1 else ''}"
        f", {points} point{'s' if points != 1 else ''})</a></p>"
    )
    body_html = "\n".join(body_parts)

    excerpt = extract_excerpt(story_text) if story_text else title
    post_id = f"hn-{object_id}"

    return {
        "post_id": post_id,
        "title": title,
        "post_url": f"/posts/{post_id}/",
        "source_url": article_url,
        "author": author,
        "author_url": f"https://news.ycombinator.com/user?id={author}",
        "avatar_url": None,
        "created_at": created_at,
        "created_at_fmt": format_date(created_at),
        "created_at_iso": format_datetime(created_at),
        "body_html": body_html,
        "excerpt": excerpt,
        "source": "hackernews",
        "section": "reading",
        "labels": [],
        "comment_count": 0,
        "comments": [],
        "metadata": {
            "hn_url": hn_url,
            "article_url": article_url,
            "points": points,
            "num_comments": num_comments,
            "hn_type": "story",
        },
    }


def _process_comment(hit: dict) -> dict | None:
    """Convert an HN comment hit into the common post schema."""
    object_id = hit.get("objectID")
    if not object_id:
        return None

    author = hit.get("author") or "unknown"
    created_at = hit.get("created_at") or ""
    comment_text = hit.get("comment_text") or ""
    story_title = hit.get("story_title") or "a Hacker News discussion"
    story_id = hit.get("story_id")

    hn_comment_url = f"{_HN_ITEM_BASE}?{urllib.parse.urlencode({'id': object_id})}"
    hn_story_url = (
        f"{_HN_ITEM_BASE}?{urllib.parse.urlencode({'id': story_id})}"
        if story_id else hn_comment_url
    )

    body_parts: list[str] = []
    if comment_text:
        # comment_text from Algolia is pre-rendered HTML produced by HN
        body_parts.append(sanitize_html(comment_text))
    body_parts.append(
        f'<p><a href="{hn_story_url}" rel="nofollow noopener noreferrer">'
        f"View full discussion on Hacker News</a></p>"
    )
    body_html = "\n".join(body_parts)

    # Plain-text excerpt: strip HTML tags from comment_text
    plain = re.sub(r"<[^>]+>", " ", comment_text)
    plain = " ".join(plain.split())
    excerpt = extract_excerpt(plain)

    post_id = f"hn-{object_id}"

    return {
        "post_id": post_id,
        "title": f"Comment on: {story_title}",
        "post_url": f"/posts/{post_id}/",
        "source_url": hn_comment_url,
        "author": author,
        "author_url": f"https://news.ycombinator.com/user?id={author}",
        "avatar_url": None,
        "created_at": created_at,
        "created_at_fmt": format_date(created_at),
        "created_at_iso": format_datetime(created_at),
        "body_html": body_html,
        "excerpt": excerpt,
        "source": "hackernews",
        "section": "reading",
        "labels": [],
        "comment_count": 0,
        "comments": [],
        "metadata": {
            "hn_url": hn_comment_url,
            "story_title": story_title,
            "story_url": hn_story_url,
            "hn_type": "comment",
        },
    }


# ---------------------------------------------------------------------------
# Public ingest entry point
# ---------------------------------------------------------------------------

def ingest(usernames: list[str]) -> list[dict]:
    """
    Fetch HN story submissions and comments for the given usernames and
    return a list of posts in the common schema.
    """
    posts: list[dict] = []
    seen_ids: set[str] = set()

    for username in usernames:
        print(f"  Fetching HN stories for: {username}")
        story_hits = _fetch_hn_items(username, "story")
        print(f"    {len(story_hits)} submission(s).")
        for hit in story_hits:
            post = _process_story(hit)
            if post and post["post_id"] not in seen_ids:
                seen_ids.add(post["post_id"])
                posts.append(post)

        print(f"  Fetching HN comments for: {username}")
        comment_hits = _fetch_hn_items(username, "comment")
        print(f"    {len(comment_hits)} comment(s).")
        for hit in comment_hits:
            post = _process_comment(hit)
            if post and post["post_id"] not in seen_ids:
                seen_ids.add(post["post_id"])
                posts.append(post)

    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts
