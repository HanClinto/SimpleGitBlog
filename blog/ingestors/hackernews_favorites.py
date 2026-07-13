"""
Hacker News Favorites ingestor for SimpleGitBlog.

Scrapes the public favorites page for a given HN username —
no API key required.

  https://news.ycombinator.com/favorites?id={username}

Pagination is handled automatically up to _MAX_ITEMS items.
"""

import re
import time
import urllib.parse
from html.parser import HTMLParser

import requests

from blog.utils import extract_excerpt, format_date, format_datetime

_HN_BASE = "https://news.ycombinator.com"
_MAX_ITEMS = 30
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ---------------------------------------------------------------------------
# HTML parser for HN listing pages
# ---------------------------------------------------------------------------

class _ListingParser(HTMLParser):
    """
    Parse items from a Hacker News listing page (front page, favorites, etc.).

    Each story is represented by two consecutive table rows:
      Row 1  (class="athing submission")  — title + URL
      Row 2                               — subtext: score, author, age, comments
    We finalize the current item when the next athing row starts (or at EOF).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.more_path: str | None = None  # relative href of the "More" pagination link

        self._cur: dict | None = None
        self._in_titleline = False
        self._got_title_href = False
        self._state: str | None = None  # 'title' | 'score' | 'user' | 'comments'

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finalize(self) -> None:
        if self._cur and self._cur.get("id") and self._cur.get("title"):
            self.items.append(dict(self._cur))
        self._cur = None
        self._in_titleline = False
        self._got_title_href = False
        self._state = None

    # ------------------------------------------------------------------
    # HTMLParser callbacks
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple]) -> None:
        a = {k: (v or "") for k, v in attrs}
        classes = a.get("class", "").split()

        # ---- Story title row ----------------------------------------
        if tag == "tr" and "athing" in classes:
            self._finalize()
            self._cur = {
                "id": a.get("id", ""),
                "title": "",
                "url": "",
                "date": "",
                "author": "",
                "points": 0,
                "num_comments": 0,
            }
            return

        if self._cur is None:
            # Only look for the "More" pagination link when no item is open
            if tag == "a" and "morelink" in classes:
                self.more_path = a.get("href", "")
            return

        # ---- Title span and its first link --------------------------
        if tag == "span" and "titleline" in classes:
            self._in_titleline = True
            return

        if self._in_titleline and tag == "a" and not self._got_title_href:
            href = a.get("href", "")
            if href and not href.startswith("from?"):
                # Resolve HN-relative links (e.g. "item?id=...")
                if not href.startswith(("http://", "https://", "//")):
                    href = f"{_HN_BASE}/{href}"
                self._cur["url"] = href
                self._state = "title"
                self._got_title_href = True
            return

        # ---- Metadata in subtext row --------------------------------
        if tag == "span" and "age" in classes:
            date = a.get("title", "")
            if date:
                # HN title format: "YYYY-MM-DDTHH:MM:SS UNIX_EPOCH" — take only the ISO part
                date = date.split()[0]
                if "T" in date and not (
                    date.endswith("Z") or "+" in date[-7:] or "-" in date[-7:]
                ):
                    date += "Z"
                self._cur["date"] = date

        elif tag == "span" and a.get("id", "").startswith("score_"):
            self._state = "score"

        elif tag == "a" and "hnuser" in classes:
            self._state = "user"

        elif tag == "a":
            href = a.get("href", "")
            item_id = self._cur.get("id", "")
            # Comments link: href contains "item?id=ITEMID"
            if item_id and f"item?id={item_id}" in href:
                self._state = "comments"

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._in_titleline:
            self._in_titleline = False
        if tag == "a" and self._state in ("title", "user", "comments"):
            self._state = None
        if tag == "span" and self._state == "score":
            self._state = None

    def handle_data(self, data: str) -> None:
        if self._cur is None or self._state is None:
            return
        s = data.strip()
        if not s:
            return
        if self._state == "title":
            self._cur["title"] += data
        elif self._state == "score":
            m = re.match(r"(\d+)", s)
            if m:
                self._cur["points"] = int(m.group(1))
        elif self._state == "user":
            self._cur["author"] = s
        elif self._state == "comments":
            clean = data.replace("\xa0", " ").strip()
            m = re.match(r"^(\d+)", clean)
            if m:
                self._cur["num_comments"] = int(m.group(1))

    def close(self) -> None:
        super().close()
        self._finalize()


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch_page(url: str, warnings: list[str]) -> tuple[str, str | None]:
    """
    Fetch one page of the HN favorites listing with simple retry on 429.
    Returns ``(html_text, next_page_url_or_None)``.
    """
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30, headers=_REQUEST_HEADERS)
            if resp.status_code == 429 and attempt < 2:
                print(f"  [Attempt {attempt + 1}/3] Got 429, sleeping {3 * (attempt + 1)}s…")
                time.sleep(3 * (attempt + 1))  # 3s then 6s
                continue
            if resp.status_code != 200:
                print(f"  [Attempt {attempt + 1}/3] HTTP {resp.status_code}")
            resp.raise_for_status()
        except requests.RequestException as exc:
            if attempt < 2:
                print(f"  [Attempt {attempt + 1}/3] Error, retrying: {exc}")
                time.sleep(3 * (attempt + 1))
                continue
            msg = f"Warning: HN favorites fetch error ({url}): {exc}"
            print(f"  {msg}")
            warnings.append(msg)
            return "", None
        print(f"  [Attempt {attempt + 1}/3] Success: {resp.status_code} ({len(resp.text)} bytes)")
        return resp.text, None
    return "", None


def _scrape_favorites(username: str, max_items: int, warnings: list[str]) -> list[dict]:
    """Scrape up to *max_items* favorites for *username*, following pagination."""
    base_url = f"{_HN_BASE}/favorites?id={urllib.parse.quote(username)}"
    url: str | None = base_url
    raw_items: list[dict] = []

    while url and len(raw_items) < max_items:
        html, _ = _fetch_page(url, warnings)
        if not html:
            break
        parser = _ListingParser()
        parser.feed(html)
        parser.close()
        raw_items.extend(parser.items)

        if parser.more_path and len(raw_items) < max_items:
            url = f"{_HN_BASE}/{parser.more_path}"
        else:
            url = None

    return raw_items[:max_items]


# ---------------------------------------------------------------------------
# Post schema conversion
# ---------------------------------------------------------------------------

def _make_post(item: dict, favoriting_user: str) -> dict | None:
    """Convert a scraped listing item into the common post schema."""
    item_id = item.get("id", "").strip()
    title = item.get("title", "").strip()
    if not item_id or not title:
        return None

    article_url = item.get("url", "").strip()
    hn_url = f"{_HN_BASE}/item?id={item_id}"
    if not article_url:
        article_url = hn_url

    author = item.get("author") or favoriting_user
    created_at = item.get("date") or ""
    points = item.get("points", 0)
    num_comments = item.get("num_comments", 0)

    body_html = (
        f'<p><a href="{hn_url}" rel="nofollow noopener noreferrer">'
        f"View HN discussion ({num_comments} comment{'s' if num_comments != 1 else ''}"
        f", {points} point{'s' if points != 1 else ''})</a></p>"
    )

    post_id = f"hn-{item_id}"

    return {
        "post_id": post_id,
        "title": title,
        "post_url": f"/posts/{post_id}/",
        "source_url": article_url,
        "author": author,
        "author_url": f"{_HN_BASE}/user?id={author}",
        "avatar_url": None,
        "created_at": created_at,
        "created_at_fmt": format_date(created_at) if created_at else "",
        "created_at_iso": format_datetime(created_at) if created_at else "",
        "body_html": body_html,
        "excerpt": extract_excerpt(title),
        "source": "hackernews",
        "section": "reading",
        "labels": [],
        "reactions": [],
        "comment_count": 0,
        "comments": [],
        "metadata": {
            "hn_url": hn_url,
            "article_url": article_url,
            "points": points,
            "num_comments": num_comments,
            "hn_type": "favorite",
        },
    }


# ---------------------------------------------------------------------------
# Public ingest entry point
# ---------------------------------------------------------------------------

def ingest(username: str) -> tuple[list[dict], list[str]]:
    """
    Scrape HN favorites for *username* and return ``(posts, warnings)``.
    Posts with missing dates or IDs are silently skipped.
    """
    warnings: list[str] = []
    print(f"  Scraping HN favorites for: {username}")
    raw_items = _scrape_favorites(username, _MAX_ITEMS, warnings)
    print(f"    {len(raw_items)} item(s) found.")

    posts: list[dict] = []
    seen_ids: set[str] = set()
    for item in raw_items:
        post = _make_post(item, username)
        if post and post["post_id"] not in seen_ids:
            seen_ids.add(post["post_id"])
            posts.append(post)

    return posts, warnings
