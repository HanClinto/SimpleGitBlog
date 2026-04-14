"""
GitHub profile helper for SimpleGitBlog.

Fetches the repository owner's public GitHub profile and linked social accounts
via the GitHub REST API.  Social links are used to auto-discover optional
integrations (e.g. YouTube channel) when explicit configuration is absent.

The social-accounts endpoint is public — no authentication required — though
a token raises the rate limit from 60 to 5 000 req/hour.

Endpoints used:
  GET /users/{username}               → name, bio, avatar, website, twitter_username
  GET /users/{username}/social_accounts → [{provider, url}, …]

Recognised social providers (non-exhaustive):
  youtube   → https://www.youtube.com/@handle  or  /channel/UCxx
  twitter   → https://twitter.com/handle  /  https://x.com/handle
  linkedin  → https://www.linkedin.com/in/slug
  twitch    → https://www.twitch.tv/handle

Social links are exposed on the config/transparency page so visitors can see
exactly where content is pulled from.
"""

from __future__ import annotations

import re
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

def fetch_owner_profile(owner: str, headers: dict) -> OwnerProfile | None:
    """
    Fetch the public GitHub profile and social accounts for ``owner``.

    Returns ``None`` if either request fails (network error, rate limit, etc.).
    Failures are non-fatal — the blog simply skips auto-discovery.
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
        print(f"  Warning: could not fetch GitHub profile for {owner}: {exc}")
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
        print(f"  Warning: could not fetch social accounts for {owner}: {exc}")
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
