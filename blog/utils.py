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


def markdown_to_safe_html(text: str) -> str:
    """Convert Markdown text to sanitized HTML."""
    if not text:
        return ""
    md = markdown.Markdown(extensions=MD_EXTENSIONS)
    raw_html = md.convert(text)
    return sanitize_html(raw_html)


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
