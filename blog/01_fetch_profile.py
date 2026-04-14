"""
Stage 01 — Fetch GitHub owner profile and social links.

Writes: _cache/profile.json

Also resolves auto-discovery of YouTube channel handles and HN usernames from
the profile's linked social accounts, so that stages 03 and 04 can read the
effective configuration without repeating the discovery logic.

Environment variables consumed:
  GITHUB_REPOSITORY   required  owner/repo
  GITHUB_TOKEN        optional  PAT for higher rate limits
  YOUTUBE_CHANNEL_IDS optional  explicit channel IDs/handles (skips auto-discovery)
  HN_USERNAME         optional  explicit HN username (skips auto-discovery)
"""

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from blog.ingestors import github_profile, youtube, hackernews  # noqa: E402
from blog.pipeline_cache import write_cache, emit_gha_warnings  # noqa: E402


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("ERROR: GITHUB_REPOSITORY is not set.", file=sys.stderr)
        sys.exit(1)

    token = os.environ.get("GITHUB_TOKEN") or None
    repo_owner = repo.split("/")[0]

    gh_headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        gh_headers["Authorization"] = f"Bearer {token}"

    warnings: list[str] = []
    start = time.monotonic()

    print(f"Fetching GitHub profile for: {repo_owner}…")
    profile = github_profile.fetch_owner_profile(repo_owner, gh_headers, warnings=warnings)

    profile_data: dict | None = None
    if profile:
        print(f"  Profile: {profile.name or profile.login}")
        print(f"  Social links: {len(profile.social_links)} found.")
        profile_data = {
            "login": profile.login,
            "name": profile.name,
            "bio": profile.bio,
            "avatar_url": profile.avatar_url,
            "website": profile.website,
            "twitter_username": profile.twitter_username,
            "social_links": [
                {"provider": s.provider, "url": s.url}
                for s in profile.social_links
            ],
        }
    else:
        print("  Could not fetch GitHub profile — social link auto-discovery disabled.")

    # --- Resolve effective YouTube channel IDs ---
    env_channel_ids = os.environ.get("YOUTUBE_CHANNEL_IDS") or None
    explicit_channel_ids = youtube.load_channel_ids(env_channel_ids)
    auto_discovered_channels: list[str] = []
    effective_youtube_channel_ids_str: str | None = env_channel_ids

    if not explicit_channel_ids and profile:
        handles = github_profile.extract_youtube_handles(profile.social_links)
        if handles:
            auto_discovered_channels = handles
            effective_youtube_channel_ids_str = ",".join(handles)
            print(f"  Auto-discovered YouTube channel(s) from GitHub profile: {handles}")

    # --- Resolve effective HN usernames ---
    env_hn = os.environ.get("HN_USERNAME") or None
    explicit_hn_usernames = hackernews.load_usernames(env_hn)
    auto_discovered_hn_username: str | None = None
    effective_hn_usernames: list[str] = explicit_hn_usernames

    if not explicit_hn_usernames and profile:
        discovered = github_profile.extract_hn_username(profile.social_links)
        if discovered:
            auto_discovered_hn_username = discovered
            effective_hn_usernames = [discovered]
            print(f"  Auto-discovered HN username from GitHub profile: {discovered}")

    elapsed = time.monotonic() - start
    emit_gha_warnings(warnings)

    write_cache("profile", {
        "stage": "profile",
        "data": profile_data,
        "effective_youtube_channel_ids_str": effective_youtube_channel_ids_str,
        "auto_discovered_channels": auto_discovered_channels,
        "effective_hn_usernames": effective_hn_usernames,
        "auto_discovered_hn_username": auto_discovered_hn_username,
        "warnings": warnings,
        "elapsed": elapsed,
        "skipped": False,
    })
    print(f"  Stage complete in {elapsed:.1f}s ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    main()
