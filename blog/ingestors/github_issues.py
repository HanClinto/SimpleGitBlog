"""
GitHub Issues ingestor for SimpleGitBlog.

Fetches issues labelled 'blog-post' from the configured repository and
converts them into the common post schema.

Section: "writing" (My Writing)
"""

import re
import urllib.parse
from pathlib import Path

import requests

from blog.utils import extract_excerpt, format_date, format_datetime, markdown_to_safe_html

BLOG_LABEL = "blog-post"
_BLOCKED_USERS_FILE = "blocked_users.txt"
_ALLOWED_POSTERS_FILE = "allowed_posters.txt"


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _github_headers(token: str | None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
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
        # Follow GitHub's Link header for the next page
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


def _fetch_blog_posts(repo: str, headers: dict) -> list:
    """Return all open issues labelled 'blog-post', newest first."""
    url = f"https://api.github.com/repos/{repo}/issues"
    params = {"labels": BLOG_LABEL, "state": "open", "sort": "created", "direction": "desc"}
    issues = _paginate(url, headers, params)
    # Exclude pull requests (GitHub returns PRs as issues too)
    return [i for i in issues if "pull_request" not in i]


def _fetch_comments(repo: str, issue_number: int, headers: dict) -> list:
    """Return all comments for an issue."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    return _paginate(url, headers)


# ---------------------------------------------------------------------------
# Config: blocked users / allowed posters
# ---------------------------------------------------------------------------

def _load_blocked_users(config_dir: Path) -> set:
    blocked: set[str] = set()
    path = config_dir / _BLOCKED_USERS_FILE
    if not path.exists():
        return blocked
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            blocked.add(line.lower())
    return blocked


def _load_allowed_posters(config_dir: Path) -> tuple[set[str], bool]:
    """
    Returns (allowed_set, open_mode).

    open_mode=True means '*' was found — all users are allowed.
    The repo owner is always implicitly allowed at call sites.
    """
    allowed: set[str] = set()
    open_mode = False
    path = config_dir / _ALLOWED_POSTERS_FILE
    if not path.exists():
        return allowed, open_mode
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "*":
            open_mode = True
        else:
            allowed.add(line.lower())
    return allowed, open_mode


def _is_allowed_poster(login: str, repo_owner: str, allowed: set, open_mode: bool) -> bool:
    if open_mode:
        return True
    if login.lower() == repo_owner.lower():
        return True
    return login.lower() in allowed


# ---------------------------------------------------------------------------
# Cross-link: detect forks
# ---------------------------------------------------------------------------

def _build_fork_blog_url(fork_owner: str, repo_name: str) -> str:
    return f"https://{fork_owner}.github.io/{urllib.parse.quote(repo_name, safe='')}/"


def _fetch_fork_owners(repo: str, headers: dict) -> set:
    url = f"https://api.github.com/repos/{repo}/forks"
    try:
        forks = _paginate(url, headers, {"per_page": 100})
        return {f["owner"]["login"].lower() for f in forks}
    except requests.HTTPError:
        return set()


# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------

def _process_issue(
    issue: dict,
    comments_raw: list,
    blocked: set,
    fork_owners: set,
    repo_name: str,
) -> dict:
    """Transform a raw GitHub issue + comments into the common post schema."""
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
            "fork_blog_url": _build_fork_blog_url(login, repo_name) if is_forker else None,
        })

    author = issue["user"]["login"]
    post_id = f"gh-{issue['number']}"

    return {
        "post_id": post_id,
        "title": issue["title"],
        "post_url": f"/posts/{post_id}/",
        "source_url": issue["html_url"],
        "author": author,
        "author_url": f"https://github.com/{author}",
        "avatar_url": issue["user"]["avatar_url"],
        "created_at": issue["created_at"],
        "created_at_fmt": format_date(issue["created_at"]),
        "created_at_iso": format_datetime(issue["created_at"]),
        "updated_at": issue.get("updated_at", issue["created_at"]),
        "body_html": markdown_to_safe_html(issue.get("body") or ""),
        "excerpt": extract_excerpt(issue.get("body") or ""),
        "source": "github",
        "section": "writing",
        "labels": [lbl["name"] for lbl in issue.get("labels", [])],
        "comment_count": len(processed_comments),
        "comments": processed_comments,
        "metadata": {
            "github_issue_url": issue["html_url"],
            "number": issue["number"],
        },
    }


# ---------------------------------------------------------------------------
# Public ingest entry point
# ---------------------------------------------------------------------------

def ingest(repo: str, token: str | None, config_dir: Path) -> list[dict]:
    """Fetch GitHub Issues and return a list of posts in the common schema."""
    headers = _github_headers(token)
    repo_owner = repo.split("/")[0]
    repo_name = repo.split("/")[-1]

    raw_issues = _fetch_blog_posts(repo, headers)
    print(f"  Found {len(raw_issues)} labelled issue(s).")

    blocked = _load_blocked_users(config_dir)
    print(f"  Blocked users: {blocked or '(none)'}")

    allowed_posters, open_mode = _load_allowed_posters(config_dir)
    if open_mode:
        print("  Allowed posters: * (open mode — all users)")
    else:
        display = allowed_posters | {repo_owner.lower()}
        print(f"  Allowed posters: {display}")

    allowed_issues = [
        i for i in raw_issues
        if _is_allowed_poster(i["user"]["login"], repo_owner, allowed_posters, open_mode)
    ]
    skipped = len(raw_issues) - len(allowed_issues)
    if skipped:
        print(f"  Skipped {skipped} issue(s) from non-allowed poster(s).")

    fork_owners = _fetch_fork_owners(repo, headers)
    print(f"  Forks found: {len(fork_owners)}")

    posts = []
    for issue in allowed_issues:
        num = issue["number"]
        print(f"  Processing issue #{num}: {issue['title']}")
        raw_comments = _fetch_comments(repo, num, headers)
        post = _process_issue(issue, raw_comments, blocked, fork_owners, repo_name)
        posts.append(post)

    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts
