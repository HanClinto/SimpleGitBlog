"""Shared utilities for SimpleGitBlog ingestors and site generation."""

import html as _html
import re
import urllib.parse
from datetime import datetime

import bleach
import markdown

# ---------------------------------------------------------------------------
# Bleach sanitization config
# ---------------------------------------------------------------------------

ALLOWED_TAGS = [
    "p", "br", "strong", "em", "b", "i", "u", "s", "del", "ins",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img",
    "hr", "div", "span",
]

ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "th": ["align"],
    "td": ["align"],
    "code": ["class"],
    "div": ["class"],
    "span": ["class"],
}

# Schemes we allow in href/src attributes
ALLOWED_PROTOCOLS = {"http", "https", "mailto"}

# ---------------------------------------------------------------------------
# Markdown extensions
# ---------------------------------------------------------------------------

MD_EXTENSIONS = [
    "fenced_code",
    "tables",
    "nl2br",
    "attr_list",
    "toc",
]


# ---------------------------------------------------------------------------
# HTML sanitization
# ---------------------------------------------------------------------------

def _is_safe_url(url: str) -> bool:
    """Return True if the URL scheme is in the allowed set (or is relative)."""
    parsed = urllib.parse.urlparse(url.strip())
    scheme = parsed.scheme.lower()
    return scheme in ALLOWED_PROTOCOLS or scheme == ""


def _attr_filter(tag: str, name: str, value: str) -> bool:
    """
    bleach attribute callable — returns True to keep an attribute, False to strip it.

    Attributes are kept only when:
    - They appear in ALLOWED_ATTRS for the given tag, AND
    - Any URL-bearing attribute (href on <a>, src on <img>) uses an allowed scheme.
    """
    allowed_for_tag = ALLOWED_ATTRS.get(tag, [])
    if name not in allowed_for_tag:
        return False
    if tag == "a" and name == "href":
        return _is_safe_url(value)
    if tag == "img" and name == "src":
        return _is_safe_url(value)
    return True


def _ensure_rel(attrs_str: str) -> str:
    """Ensure rel="nofollow noopener noreferrer" is present on an <a> tag's attribute string."""
    rel_value = "nofollow noopener noreferrer"
    if re.search(r'\brel=', attrs_str, re.IGNORECASE):
        attrs_str = re.sub(
            r'\brel=["\'][^"\']*["\']',
            f'rel="{rel_value}"',
            attrs_str,
            flags=re.IGNORECASE,
        )
    else:
        attrs_str = attrs_str.rstrip() + f' rel="{rel_value}"'
    return (" " + attrs_str.strip()) if attrs_str.strip() else f' rel="{rel_value}"'


def sanitize_html(raw_html: str) -> str:
    """Sanitize HTML using bleach with a strict allowlist."""
    cleaned = bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=_attr_filter,
        strip=True,
        strip_comments=True,
    )
    # Add rel="nofollow noopener noreferrer" to every <a> tag.
    cleaned = re.sub(
        r'<a(\s[^>]*)?>',
        lambda m: '<a' + _ensure_rel(m.group(1) or "") + ">",
        cleaned,
    )
    return cleaned


# ---------------------------------------------------------------------------
# YouTube URL auto-embedding
# ---------------------------------------------------------------------------

# YouTube video ID: exactly 11 characters of [A-Za-z0-9_-]
# Matches the URL forms YouTube uses in the wild (with optional www / m prefix)
_YT_URL_PATTERNS = [
    # https://www.youtube.com/watch?v=VIDEO_ID  (plus optional extra params)
    re.compile(r'https?://(?:www\.|m\.)?youtube\.com/watch\?(?:[^"<>\s]*&)?v=([A-Za-z0-9_-]{11})(?:[^"<>\s]*)?', re.IGNORECASE),
    # https://youtu.be/VIDEO_ID
    re.compile(r'https?://youtu\.be/([A-Za-z0-9_-]{11})(?:[?#][^"<>\s]*)?', re.IGNORECASE),
    # https://www.youtube.com/embed/VIDEO_ID  (already an embed URL pasted in)
    re.compile(r'https?://(?:www\.)?youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]{11})(?:[?#][^"<>\s]*)?', re.IGNORECASE),
    # https://www.youtube.com/shorts/VIDEO_ID
    re.compile(r'https?://(?:www\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})(?:[?#][^"<>\s]*)?', re.IGNORECASE),
]


def _extract_youtube_video_id(url: str) -> str | None:
    """Return the video ID from a YouTube URL, or None if not recognised."""
    for pattern in _YT_URL_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def _make_youtube_embed(video_id: str, title: str = "YouTube video") -> str:
    """Return an accessible, privacy-enhanced YouTube embed HTML string."""
    # youtube-nocookie.com doesn't set cookies until the user presses play.
    escaped_id    = urllib.parse.quote(video_id, safe="")
    escaped_title = _html.escape(title, quote=True)
    src = f"https://www.youtube-nocookie.com/embed/{escaped_id}"
    return (
        f'<div class="video-embed">'
        f'<iframe src="{src}" title="{escaped_title}" frameborder="0" '
        f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
        f'gyroscope; picture-in-picture" allowfullscreen></iframe>'
        f'</div>'
    )


def embed_youtube_urls(html: str) -> str:
    """
    Replace standalone YouTube URL paragraphs with responsive embedded iframes.

    A "standalone" paragraph is one whose only content is a YouTube URL —
    either as plain text or wrapped in an ``<a>`` tag (the typical output of
    Markdown processors for bare URLs).  Inline links that are part of a
    longer sentence are intentionally left as links.

    The embed uses ``youtube-nocookie.com`` for privacy (no cookies until
    the user presses play).  The iframe is injected by this function — it is
    *not* user-supplied HTML — so it is safe despite iframes being stripped
    from user content by the sanitizer.
    """
    # Pattern: <p> whose trimmed content is either:
    #   • a bare YouTube URL, OR
    #   • <a ...>YouTube URL</a>  (Markdown linkified the bare URL)
    # We capture whatever is between <p> and </p> and inspect it.
    _para_re = re.compile(r'<p>(.*?)</p>', re.DOTALL | re.IGNORECASE)

    def _maybe_embed(m: re.Match) -> str:
        inner = m.group(1).strip()

        # Case 1: bare URL with no markup at all — the entire paragraph is the URL
        if not inner.startswith("<"):
            if re.fullmatch(r'https?://\S+', inner):
                video_id = _extract_youtube_video_id(inner)
                if video_id:
                    return _make_youtube_embed(video_id)
            return m.group(0)

        # Case 2: <a href="URL">…</a> — pick the href
        href_m = re.match(
            r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>.*?</a>\s*$',
            inner,
            re.DOTALL | re.IGNORECASE,
        )
        if href_m:
            video_id = _extract_youtube_video_id(href_m.group(1))
            if video_id:
                # Extract the link text to use as the iframe title
                link_text_m = re.search(r'>([^<]*)<', inner)
                title = link_text_m.group(1).strip() if link_text_m else "YouTube video"
                return _make_youtube_embed(video_id, title or "YouTube video")

        return m.group(0)

    return _para_re.sub(_maybe_embed, html)


def markdown_to_safe_html(text: str) -> str:
    """Convert Markdown text to sanitized HTML, with YouTube URL auto-embedding."""
    if not text:
        return ""
    md = markdown.Markdown(extensions=MD_EXTENSIONS)
    raw_html = md.convert(text)
    sanitized = sanitize_html(raw_html)
    return embed_youtube_urls(sanitized)


def plain_text_to_html(text: str) -> str:
    """Convert plain text to safe HTML, preserving paragraph breaks."""
    if not text:
        return ""
    escaped = _html.escape(text)
    paragraphs = re.split(r'\n{2,}', escaped)
    parts = ['<p>' + p.replace('\n', '<br>') + '</p>'
             for p in paragraphs if p.strip()]
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Excerpt / date helpers
# ---------------------------------------------------------------------------

def extract_excerpt(text: str, max_chars: int = 280) -> str:
    """Return a plain-text excerpt from a Markdown or plain-text body."""
    # Strip Markdown syntax roughly
    plain = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    plain = re.sub(r"`[^`]+`", "", plain)
    plain = re.sub(r"!\[.*?\]\(.*?\)", "", plain)
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
    plain = re.sub(r"[#*_~>]+", "", plain)
    plain = " ".join(plain.split())
    if len(plain) > max_chars:
        plain = plain[:max_chars].rsplit(" ", 1)[0] + "…"
    return plain


def format_date(iso_string: str) -> str:
    """Convert ISO 8601 date string to a human-readable format."""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y").replace(" 0", " ")
    except (ValueError, AttributeError):
        return iso_string


def format_datetime(iso_string: str) -> str:
    """Convert ISO 8601 datetime string for <time> datetime attribute."""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.isoformat()
    except (ValueError, AttributeError):
        return iso_string
