"""GitHub merged pull-request ingestor for public contributions."""

from __future__ import annotations

import urllib.parse

import requests

from blog.utils import format_date, format_datetime


def _github_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _repository_from_api_url(repository_url: str) -> str:
    marker = "/repos/"
    if marker in repository_url:
        return repository_url.rsplit(marker, 1)[-1]
    parts = [part for part in repository_url.split("/") if part]
    return "/".join(parts[-2:]) if len(parts) >= 2 else repository_url


def ingest(user: str, token: str | None, limit: int = 8) -> tuple[list[dict], list[str]]:
    """Return recent merged PRs authored by ``user`` outside their own repos."""
    warnings: list[str] = []
    if not user or limit <= 0:
        return [], warnings

    query = f"author:{user} is:pr is:merged -user:{user}"
    url = "https://api.github.com/search/issues"
    params = {
        "q": query,
        "sort": "updated",
        "order": "desc",
        "per_page": max(limit * 2, 10),
    }

    try:
        response = requests.get(url, headers=_github_headers(token), params=params, timeout=30)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return [], [f"Warning: could not fetch GitHub contributions (HTTP {status})."]
    except requests.RequestException as exc:
        return [], [f"Warning: could not fetch GitHub contributions: {exc}"]

    posts: list[dict] = []
    payload = response.json()
    for item in payload.get("items", []):
        repository = _repository_from_api_url(item.get("repository_url", ""))
        if not repository or repository.lower().startswith(f"{user.lower()}/"):
            continue

        title = item.get("title", "")
        url = item.get("html_url", "")
        closed_at = item.get("closed_at") or item.get("updated_at") or item.get("created_at")
        if not title or not url or not closed_at:
            continue

        safe_repo = urllib.parse.quote(repository.replace("/", "-"), safe="")
        number = item.get("number") or len(posts) + 1
        posts.append({
            "post_id": f"gh-pr-{safe_repo}-{number}",
            "title": title,
            "post_url": url,
            "source_url": url,
            "author": user,
            "author_url": f"https://github.com/{user}",
            "avatar_url": item.get("user", {}).get("avatar_url"),
            "created_at": closed_at,
            "created_at_fmt": format_date(closed_at),
            "created_at_iso": format_datetime(closed_at),
            "body_html": "",
            "excerpt": item.get("body") or "",
            "source": "github_contribution",
            "section": "contributions",
            "labels": ["Merged PR"],
            "reactions": [],
            "comment_count": item.get("comments", 0),
            "comments": [],
            "metadata": {
                "repository": repository,
                "number": number,
                "state": item.get("state", "closed"),
            },
        })
        if len(posts) >= limit:
            break

    return posts, warnings
