"""
SimpleGitBlog - Static blog generator powered by GitHub Issues.

Usage:
    Set environment variables and run:
        python blog/generate.py

Environment variables:
    GITHUB_TOKEN       Optional. GitHub personal access token for higher API rate limits.
    GITHUB_REPOSITORY  Required. Repository in "owner/repo" format.
    OUTPUT_DIR         Optional. Output directory for the generated site. Default: _site
"""

import os
import re
import shutil
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import bleach
import markdown
import requests
from jinja2 import Environment, FileSystemLoader

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
TEMPLATES_DIR = SCRIPT_DIR / "templates"
STATIC_DIR = SCRIPT_DIR / "static"
CONFIG_DIR = SCRIPT_DIR.parent / "config"
BLOCKED_USERS_FILE = CONFIG_DIR / "blocked_users.txt"

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

# Attributes allowed on wildcard (any) tag
ALLOWED_ATTRS_WILDCARD: list[str] = []

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
# GitHub API helpers
# ---------------------------------------------------------------------------

BLOG_LABEL = "blog-post"


def _github_headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _paginate(url: str, headers: dict, params: dict | None = None) -> list:
    """Fetch all pages from a GitHub API endpoint."""
    results = []
    params = dict(params or {})
    params.setdefault("per_page", 100)
    while url:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        results.extend(response.json())
        # Follow GitHub's Link header for next page
        url = None
        params = {}  # params are baked into the next URL
        link_header = response.headers.get("Link", "")
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                match = re.search(r"<([^>]+)>", part)
                if match:
                    url = match.group(1)
    return results


def fetch_blog_posts(repo: str, headers: dict) -> list:
    """Return all open issues labelled 'blog-post', newest first."""
    url = f"https://api.github.com/repos/{repo}/issues"
    params = {"labels": BLOG_LABEL, "state": "open", "sort": "created", "direction": "desc"}
    issues = _paginate(url, headers, params)
    # Exclude pull requests (GitHub returns PRs as issues)
    return [i for i in issues if "pull_request" not in i]


def fetch_comments(repo: str, issue_number: int, headers: dict) -> list:
    """Return all comments for an issue."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    return _paginate(url, headers)


# ---------------------------------------------------------------------------
# Blocked users
# ---------------------------------------------------------------------------

def load_blocked_users() -> set:
    """Load blocked usernames from config file."""
    blocked = set()
    if not BLOCKED_USERS_FILE.exists():
        return blocked
    for line in BLOCKED_USERS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            blocked.add(line.lower())
    return blocked


# ---------------------------------------------------------------------------
# Content processing
# ---------------------------------------------------------------------------

def _is_safe_url(url: str) -> bool:
    """Return True if the URL scheme is in the allowed set (or is relative)."""
    parsed = urllib.parse.urlparse(url.strip())
    scheme = parsed.scheme.lower()
    return scheme in ALLOWED_PROTOCOLS or scheme == ""


def _attr_filter(tag: str, name: str, value: str) -> bool:
    """
    bleach attribute callable — returns True to keep an attribute.
    Rejects attributes not in ALLOWED_ATTRS and strips dangerous URL schemes.
    """
    allowed_for_tag = ALLOWED_ATTRS.get(tag, [])
    if name not in allowed_for_tag:
        return False
    if tag == "a" and name == "href":
        return _is_safe_url(value)
    if tag == "img" and name == "src":
        return _is_safe_url(value)
    return True


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
    # The (\s[^>]*)? group makes the attributes portion optional so bare <a> is also matched.
    cleaned = re.sub(
        r'<a(\s[^>]*)?>',
        lambda m: '<a' + _ensure_rel(m.group(1) or "") + ">",
        cleaned,
    )
    return cleaned


def _ensure_rel(attrs_str: str) -> str:
    """Prepend a safe space+attrs block that always includes rel=nofollow."""
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


def markdown_to_safe_html(text: str) -> str:
    """Convert Markdown text to sanitized HTML."""
    if not text:
        return ""
    md = markdown.Markdown(extensions=MD_EXTENSIONS)
    raw_html = md.convert(text)
    return sanitize_html(raw_html)


def extract_excerpt(text: str, max_chars: int = 280) -> str:
    """Return a plain-text excerpt from a Markdown body."""
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


# ---------------------------------------------------------------------------
# Cross-link: detect forks
# ---------------------------------------------------------------------------

def build_fork_blog_url(fork_owner: str, repo_name: str) -> str:
    """
    Given a commenter who may have forked the repo, return the likely
    GitHub Pages URL for their fork's blog.
    """
    return f"https://{fork_owner}.github.io/{repo_name}/"


def fetch_fork_owners(repo: str, headers: dict) -> set:
    """Return set of GitHub usernames who have forked this repo."""
    url = f"https://api.github.com/repos/{repo}/forks"
    try:
        forks = _paginate(url, headers, {"per_page": 100})
        return {f["owner"]["login"].lower() for f in forks}
    except requests.HTTPError:
        return set()


# ---------------------------------------------------------------------------
# Site generation
# ---------------------------------------------------------------------------

def process_issue(issue: dict, comments_raw: list, blocked: set, fork_owners: set, repo_name: str) -> dict:
    """Transform a raw GitHub issue + comments into template-ready data."""
    # Filter blocked users from comments
    comments = [
        c for c in comments_raw
        if c["user"]["login"].lower() not in blocked
    ]

    processed_comments = []
    for c in comments:
        login = c["user"]["login"]
        is_forker = login.lower() in fork_owners
        processed_comments.append({
            "id": c["id"],
            "author": login,
            "author_url": f"https://github.com/{login}",
            "avatar_url": c["user"]["avatar_url"],
            "created_at": c["created_at"],
            "created_at_fmt": format_date(c["created_at"]),
            "created_at_iso": format_datetime(c["created_at"]),
            "body_html": markdown_to_safe_html(c.get("body") or ""),
            "fork_blog_url": build_fork_blog_url(login, repo_name) if is_forker else None,
        })

    author = issue["user"]["login"]
    return {
        "number": issue["number"],
        "title": issue["title"],
        "author": author,
        "author_url": f"https://github.com/{author}",
        "avatar_url": issue["user"]["avatar_url"],
        "created_at": issue["created_at"],
        "created_at_fmt": format_date(issue["created_at"]),
        "created_at_iso": format_datetime(issue["created_at"]),
        "updated_at": issue.get("updated_at", issue["created_at"]),
        "body_html": markdown_to_safe_html(issue.get("body") or ""),
        "excerpt": extract_excerpt(issue.get("body") or ""),
        "comments": processed_comments,
        "comment_count": len(processed_comments),
        "github_issue_url": issue["html_url"],
        "labels": [lbl["name"] for lbl in issue.get("labels", [])],
    }


def copy_static(output_dir: Path) -> None:
    static_out = output_dir / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(STATIC_DIR, static_out)


def write_nojekyll(output_dir: Path) -> None:
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")


def generate_site(
    repo: str,
    token: str | None,
    output_dir: Path,
) -> None:
    headers = _github_headers(token)
    repo_name = repo.split("/")[-1]
    repo_url = f"https://github.com/{repo}"

    print(f"Fetching blog posts from {repo}…")
    raw_issues = fetch_blog_posts(repo, headers)
    print(f"  Found {len(raw_issues)} post(s).")

    blocked = load_blocked_users()
    print(f"  Blocked users: {blocked or '(none)'}")

    print("Fetching fork owners for cross-link support…")
    fork_owners = fetch_fork_owners(repo, headers)
    print(f"  Forks found: {len(fork_owners)}")

    posts = []
    for issue in raw_issues:
        num = issue["number"]
        print(f"  Processing issue #{num}: {issue['title']}")
        raw_comments = fetch_comments(repo, num, headers)
        post = process_issue(issue, raw_comments, blocked, fork_owners, repo_name)
        posts.append(post)

    # Sort newest-first by creation date
    posts.sort(key=lambda p: p["created_at"], reverse=True)

    # Set up Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    env.globals["repo_url"] = repo_url
    env.globals["repo_name"] = repo_name
    env.globals["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Render index page
    index_tmpl = env.get_template("index.html")
    index_html = index_tmpl.render(posts=posts)
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    print("Wrote index.html")

    # Render individual post pages
    post_tmpl = env.get_template("post.html")
    for post in posts:
        post_dir = output_dir / "posts" / str(post["number"])
        post_dir.mkdir(parents=True, exist_ok=True)
        post_html = post_tmpl.render(post=post)
        (post_dir / "index.html").write_text(post_html, encoding="utf-8")
        print(f"  Wrote posts/{post['number']}/index.html")

    # Copy static assets
    copy_static(output_dir)
    print("Copied static assets.")

    # Disable Jekyll
    write_nojekyll(output_dir)
    print("Wrote .nojekyll")

    print(f"\nDone! Site generated in {output_dir}/")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("ERROR: GITHUB_REPOSITORY environment variable is not set.", file=sys.stderr)
        print("  Example: GITHUB_REPOSITORY=owner/repo python blog/generate.py", file=sys.stderr)
        sys.exit(1)

    token = os.environ.get("GITHUB_TOKEN") or None
    output_dir = Path(os.environ.get("OUTPUT_DIR", "_site")).resolve()

    generate_site(repo=repo, token=token, output_dir=output_dir)


if __name__ == "__main__":
    main()
