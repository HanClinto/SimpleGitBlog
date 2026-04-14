"""
GitHub Issues ingestor for SimpleGitBlog.

Fetches all open issues authored by repository collaborators (users with
write access or higher) and converts them into the common post schema.
No special label is required — every issue from an authorised author is
a blog post.

Section: "writing" (My Writing)
"""

import re
import sys
import unicodedata
import urllib.parse
from pathlib import Path

import requests

from blog.utils import extract_excerpt, format_date, format_datetime, markdown_to_safe_html

_BLOCKED_USERS_FILE = "blocked_users.txt"

# Maps GitHub reaction keys to display emoji + accessible label
_REACTION_MAP = [
    ("+1",      "👍", "thumbs up"),
    ("-1",      "👎", "thumbs down"),
    ("laugh",   "😄", "laugh"),
    ("hooray",  "🎉", "hooray"),
    ("confused","😕", "confused"),
    ("heart",   "❤️", "heart"),
    ("rocket",  "🚀", "rocket"),
    ("eyes",    "👀", "eyes"),
]


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


def _fetch_all_issues(repo: str, headers: dict) -> list:
    """Return all open issues (no label filter), newest first."""
    url = f"https://api.github.com/repos/{repo}/issues"
    params = {"state": "open", "sort": "created", "direction": "desc"}
    issues = _paginate(url, headers, params)
    # Exclude pull requests (GitHub returns PRs as issues too)
    return [i for i in issues if "pull_request" not in i]


def _fetch_comments(repo: str, issue_number: int, headers: dict) -> list:
    """Return all comments for an issue."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    return _paginate(url, headers)


# ---------------------------------------------------------------------------
# Config: blocked users
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


# ---------------------------------------------------------------------------
# Collaborators: who has write access to this repo?
# ---------------------------------------------------------------------------

def _fetch_write_collaborators(repo: str, headers: dict) -> set[str]:
    """
    Return the set of logins that have push (write), maintain, or admin
    access to the repository.  Falls back to an empty set on any API error
    (e.g. unauthenticated requests or insufficient token scope).
    """
    url = f"https://api.github.com/repos/{repo}/collaborators"
    try:
        collaborators = _paginate(url, headers, {"affiliation": "all"})
        allowed: set[str] = set()
        for c in collaborators:
            perms = c.get("permissions", {})
            if perms.get("push") or perms.get("maintain") or perms.get("admin"):
                allowed.add(c["login"].lower())
        return allowed
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        print(
            f"  Warning: could not fetch collaborators (HTTP {status})."
            " Falling back to owner-only allow-list."
            " Ensure the workflow token has repository read access.",
            file=sys.stderr,
        )
        return set()


def _is_allowed_poster(login: str, repo_owner: str, collaborators: set[str]) -> bool:
    if login.lower() == repo_owner.lower():
        return True
    return login.lower() in collaborators


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------

def _parse_reactions(reactions_raw: dict | None) -> list[dict]:
    """Convert a GitHub reactions object to a list of {emoji, label, count}."""
    if not reactions_raw:
        return []
    result = []
    for key, emoji, label in _REACTION_MAP:
        count = reactions_raw.get(key, 0)
        if count > 0:
            result.append({"emoji": emoji, "label": label, "count": count})
    return result


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
    repo: str,
    repo_name: str,
) -> dict:
    """Transform a raw GitHub issue + comments into the common post schema."""
    repo_owner_lc = repo.split("/")[0].lower()
    issue_number = issue["number"]

    comments = [
        c for c in comments_raw
        if c["user"]["login"].lower() not in blocked
    ]

    processed_comments = []
    for c in comments:
        login = c["user"]["login"]
        is_forker = login.lower() in fork_owners
        comment_url = (
            f"https://github.com/{repo}/issues/{issue_number}"
            f"#issuecomment-{c['id']}"
        )
        processed_comments.append({
            "id": c["id"],
            "author": login,
            "author_url": f"https://github.com/{login}",
            "avatar_url": c["user"]["avatar_url"],
            "created_at": c["created_at"],
            "created_at_fmt": format_date(c["created_at"]),
            "created_at_iso": format_datetime(c["created_at"]),
            "comment_url": comment_url,
            "body_html": markdown_to_safe_html(c.get("body") or ""),
            "reactions": _parse_reactions(c.get("reactions")),
            "fork_blog_url": _build_fork_blog_url(login, repo_name) if is_forker else None,
        })

    author = issue["user"]["login"]
    post_id = f"gh-{issue_number}"

    # Filter out the internal "blog-post" label if it was historically used
    labels = [
        lbl["name"] for lbl in issue.get("labels", [])
        if lbl["name"] != "blog-post"
    ]

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
        "labels": labels,
        "reactions": _parse_reactions(issue.get("reactions")),
        "comment_count": len(processed_comments),
        "comments": processed_comments,
        "metadata": {
            "github_issue_url": issue["html_url"],
            "number": issue_number,
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

    raw_issues = _fetch_all_issues(repo, headers)
    print(f"  Found {len(raw_issues)} open issue(s).")

    blocked = _load_blocked_users(config_dir)
    print(f"  Blocked users: {blocked or '(none)'}")

    # Primary allowed list: repo owner + collaborators with write+ access
    collaborators = _fetch_write_collaborators(repo, headers)
    display = collaborators | {repo_owner.lower()}
    print(f"  Allowed posters (owner + write-access collaborators): {display}")

    allowed_issues = [
        i for i in raw_issues
        if _is_allowed_poster(i["user"]["login"], repo_owner, collaborators)
    ]
    skipped = len(raw_issues) - len(allowed_issues)
    if skipped:
        print(f"  Skipped {skipped} issue(s) from non-collaborator author(s).")

    fork_owners = _fetch_fork_owners(repo, headers)
    print(f"  Forks found: {len(fork_owners)}")

    posts = []
    for issue in allowed_issues:
        num = issue["number"]
        print(f"  Processing issue #{num}: {issue['title']}")
        raw_comments = _fetch_comments(repo, num, headers)
        post = _process_issue(issue, raw_comments, blocked, fork_owners, repo, repo_name)
        posts.append(post)

    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts
