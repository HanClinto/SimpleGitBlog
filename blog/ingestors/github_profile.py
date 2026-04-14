"""
GitHub profile helper for SimpleGitBlog.

Fetches the repository owner's public GitHub profile and linked social accounts
via the GitHub REST API.  Social links are used to auto-discover optional
integrations (YouTube channel, Hacker News username, etc.) when explicit
configuration is absent.

The social-accounts endpoint is public — no authentication required — though
a token raises the rate limit from 60 to 5 000 req/hour.

Endpoints used:
  GET /users/{username}               → name, bio, avatar, website, twitter_username
  GET /users/{username}/social_accounts → [{provider, url}, …]

Recognised social providers and how they are used:
  hackernews → https://news.ycombinator.com/user?id=username
               Auto-discovers HN_USERNAME when env var not set.
  youtube    → https://www.youtube.com/@handle  or  /channel/UCxx
               Auto-discovers YOUTUBE_CHANNEL_IDS when env var not set.
  twitter    → https://twitter.com/handle  /  https://x.com/handle
               Displayed on config page (future integration).
  linkedin   → https://www.linkedin.com/in/slug
               Displayed on config page (future integration).

Social links are exposed on the config/transparency page so visitors can see
exactly where content is pulled from.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import NamedTuple

import requests

_GITHUB_API = "https://api.github.com"
_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class SocialLink(NamedTuple):
    provider: str   # e.g. "youtube", "twitter", "linkedin"
    url: str        # canonical URL as stored on GitHub


class OwnerProfile(NamedTuple):
    login: str
    name: str | None
    bio: str | None
    avatar_url: str | None
    website: str | None             # the "blog" field on the profile
    twitter_username: str | None    # dedicated twitter_username field
    social_links: list[SocialLink]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_owner_profile(
    owner: str,
    headers: dict,
    warnings: list[str] | None = None,
) -> OwnerProfile | None:
    """
    Fetch the public GitHub profile and social accounts for ``owner``.

    Returns ``None`` if either request fails (network error, rate limit, etc.).
    Failures are non-fatal — the blog simply skips auto-discovery.

    Any warning messages are printed and, when ``warnings`` is provided,
    appended to that list for downstream reporting.
    """
    # --- Basic profile ---
    try:
        resp = requests.get(
            f"{_GITHUB_API}/users/{owner}",
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        profile_data = resp.json()
    except requests.RequestException as exc:
        msg = f"Warning: could not fetch GitHub profile for {owner}: {exc}"
        print(f"  {msg}")
        if warnings is not None:
            warnings.append(msg)
        return None

    # --- Social accounts ---
    social_links: list[SocialLink] = []
    try:
        resp2 = requests.get(
            f"{_GITHUB_API}/users/{owner}/social_accounts",
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp2.raise_for_status()
        for item in resp2.json():
            provider = (item.get("provider") or "").lower().strip()
            url = (item.get("url") or "").strip()
            if provider and url:
                social_links.append(SocialLink(provider=provider, url=url))
    except requests.RequestException as exc:
        msg = f"Warning: could not fetch social accounts for {owner}: {exc}"
        print(f"  {msg}")
        if warnings is not None:
            warnings.append(msg)
        # Non-fatal — continue with an empty list

    return OwnerProfile(
        login=profile_data.get("login") or owner,
        name=profile_data.get("name") or None,
        bio=profile_data.get("bio") or None,
        avatar_url=profile_data.get("avatar_url") or None,
        website=profile_data.get("blog") or None,
        twitter_username=profile_data.get("twitter_username") or None,
        social_links=social_links,
    )


# ---------------------------------------------------------------------------
# Social link extraction helpers
# ---------------------------------------------------------------------------

def extract_youtube_handles(social_links: list[SocialLink]) -> list[str]:
    """
    Return YouTube channel handles or IDs found in the owner's social links.

    Recognised URL forms:
      https://www.youtube.com/@handle
      https://youtube.com/@handle
      https://www.youtube.com/channel/UCxxxxxxxx
      https://www.youtube.com/c/custom-name   (treated as @custom-name)
      https://www.youtube.com/user/username   (treated as @username)

    Returns a list of strings that can be passed directly to
    ``youtube.load_channel_ids()`` or ``youtube._resolve_channel_id()``.
    """
    handles: list[str] = []
    for link in social_links:
        if link.provider != "youtube":
            continue
        url = link.url.rstrip("/")

        # @handle form — handles only allow letters, digits, underscores, hyphens
        m = re.search(r'youtube\.com/(@[A-Za-z0-9_-]+)', url, re.IGNORECASE)
        if m:
            handles.append(m.group(1))
            continue

        # /channel/UCxxxxxxxx (UC + exactly 22 base64-ish chars)
        m = re.search(r'youtube\.com/channel/(UC[A-Za-z0-9_-]{22})', url, re.IGNORECASE)
        if m:
            handles.append(m.group(1))
            continue

        # /c/slug or /user/slug — treat as @slug (allow dots for legacy /user/ names)
        m = re.search(r'youtube\.com/(?:c|user)/([A-Za-z0-9_-]+)', url, re.IGNORECASE)
        if m:
            handles.append(f"@{m.group(1)}")
            continue

    return handles


def extract_twitter_url(social_links: list[SocialLink]) -> str | None:
    """Return the first Twitter / X social link URL, or None."""
    for link in social_links:
        if link.provider in ("twitter", "x"):
            return link.url
    return None


def extract_linkedin_url(social_links: list[SocialLink]) -> str | None:
    """Return the first LinkedIn social link URL, or None."""
    for link in social_links:
        if link.provider == "linkedin":
            return link.url
    return None


def extract_hn_username(social_links: list[SocialLink]) -> str | None:
    """
    Return the Hacker News username found in the owner's social links, or None.

    GitHub stores HN accounts with provider ``"hackernews"`` (sometimes
    ``"hacker-news"`` in older entries) and URLs of the form:
      https://news.ycombinator.com/user?id=username

    The username is extracted from the ``id`` query parameter.
    """
    _HN_PROVIDERS = {"hackernews", "hacker-news", "hn"}

    for link in social_links:
        if link.provider.lower() in _HN_PROVIDERS:
            # Extract username from ?id= query param
            parsed = urllib.parse.urlparse(link.url)
            params = urllib.parse.parse_qs(parsed.query)
            ids = params.get("id", [])
            if ids and ids[0].strip():
                return ids[0].strip()
            # Fallback: bare username in path (e.g. news.ycombinator.com/~username)
            path_m = re.search(r'/~?([A-Za-z0-9_-]+)$', parsed.path)
            if path_m:
                return path_m.group(1)

        # Provider-agnostic fallback: match any HN user URL regardless of provider label
        if re.match(r'https?://news\.ycombinator\.com/user', link.url, re.IGNORECASE):
            parsed = urllib.parse.urlparse(link.url)
            params = urllib.parse.parse_qs(parsed.query)
            ids = params.get("id", [])
            if ids and ids[0].strip():
                return ids[0].strip()

    return None
