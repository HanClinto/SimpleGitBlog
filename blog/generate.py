"""
SimpleGitBlog - Static blog generator.

Orchestrates all configured ingestors, merges their output, and renders a
static site with posts grouped into thematic sections.

Usage:
    python blog/generate.py

Environment variables:
    GITHUB_TOKEN         Optional. GitHub personal access token (higher API rate limits).
    GITHUB_REPOSITORY    Required. Repository in "owner/repo" format.
    OUTPUT_DIR           Optional. Output directory. Default: _site

    YOUTUBE_API_KEY      Removed — no longer needed! The YouTube ingestor now uses
                         YouTube's public Atom/RSS feeds (no key required).
    YOUTUBE_PLAYLIST_IDS Optional. YouTube playlist IDs, one per line.
                         Set this as a GitHub Actions repository variable
                         (Settings → Variables) so it is NOT stored in source.

    HN_USERNAME          Optional. Hacker News username.
                         Required to enable the "My Reading" section.
                         Set this as a GitHub Actions repository variable
                         (Settings → Variables) so it is NOT stored in source.
"""

import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path so package imports work when this
# script is invoked directly (python blog/generate.py) from the repo root.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from jinja2 import Environment, FileSystemLoader  # noqa: E402
import urllib.parse  # noqa: E402

from blog.ingestors import github_issues, github_profile, hackernews, youtube  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
TEMPLATES_DIR = SCRIPT_DIR / "templates"
STATIC_DIR = SCRIPT_DIR / "static"
CONFIG_DIR = SCRIPT_DIR.parent / "config"

# ---------------------------------------------------------------------------
# Section definitions — order controls display order on the site
# ---------------------------------------------------------------------------

_SECTION_DEFS = [
    {"key": "writing", "title": "My Writing",  "icon": "✍️"},
    {"key": "videos",   "title": "My Videos",   "icon": "🎥"},
    {"key": "watching", "title": "My Watching", "icon": "📺"},
    {"key": "reading",  "title": "My Reading",  "icon": "📰"},
]

# ---------------------------------------------------------------------------
# Static asset helpers
# ---------------------------------------------------------------------------


def copy_static(output_dir: Path) -> None:
    static_out = output_dir / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(STATIC_DIR, static_out)


def write_nojekyll(output_dir: Path) -> None:
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")


def label_slug(label: str) -> str:
    """Convert a label name to a URL-safe path segment.

    ASCII-only labels become clean hyphenated slugs (e.g. "bug fix" → "bug-fix").
    Labels that are entirely non-ASCII (e.g. emoji-only "🚀") fall back to
    percent-encoding so the slug is always unique and valid in a URL path.
    """
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or urllib.parse.quote(label, safe="")


# ---------------------------------------------------------------------------
# Site generation
# ---------------------------------------------------------------------------


def generate_site(
    repo: str,
    token: str | None,
    output_dir: Path,
    youtube_playlist_ids: str | None = None,
    youtube_channel_ids: str | None = None,
    hn_usernames: list[str] | None = None,
) -> None:
    _start = time.monotonic()

    repo_owner = repo.split("/")[0]
    repo_name = repo.split("/")[-1]
    repo_url = f"https://github.com/{repo}"

    # Build GitHub API request headers
    gh_headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        gh_headers["Authorization"] = f"Bearer {token}"

    # Load config files early so we can pass them to the config page
    hidden_labels = github_issues._load_hidden_labels(CONFIG_DIR)
    blocked_users = github_issues._load_blocked_users(CONFIG_DIR)

    # --- GitHub owner profile & social links ---
    print(f"Fetching GitHub profile for: {repo_owner}…")
    owner_profile = github_profile.fetch_owner_profile(repo_owner, gh_headers)
    if owner_profile:
        print(f"  Profile: {owner_profile.name or owner_profile.login}")
        print(f"  Social links: {len(owner_profile.social_links)} found.")
    else:
        print("  Could not fetch GitHub profile — social link auto-discovery disabled.")

    # --- GitHub Issues (My Writing) — always runs ---
    print("Fetching GitHub Issues (My Writing)…")
    writing_posts = github_issues.ingest(repo, token, CONFIG_DIR)
    print(f"  {len(writing_posts)} post(s) ingested from GitHub Issues.")

    # --- YouTube playlists & channels (My Watching) — uses free public RSS feeds, no API key ---
    watching_posts: list[dict] = []
    playlist_ids = youtube.load_playlist_ids(youtube_playlist_ids)
    channel_ids = youtube.load_channel_ids(youtube_channel_ids)

    # Auto-discover YouTube channel from GitHub social links when not explicitly configured
    profile_youtube_handles: list[str] = []
    if owner_profile:
        profile_youtube_handles = github_profile.extract_youtube_handles(owner_profile.social_links)
    auto_discovered_channels = (
        profile_youtube_handles
        if (not channel_ids and profile_youtube_handles)
        else []
    )
    effective_channel_ids_str = youtube_channel_ids
    if auto_discovered_channels and not channel_ids:
        print(f"  Auto-discovered YouTube channel(s) from GitHub profile: {auto_discovered_channels}")
        effective_channel_ids_str = ",".join(auto_discovered_channels)
        channel_ids = youtube.load_channel_ids(effective_channel_ids_str)

    if playlist_ids or channel_ids:
        print("Fetching YouTube content (My Watching)…")
        watching_posts = youtube.ingest(youtube_playlist_ids, effective_channel_ids_str)
        print(f"  {len(watching_posts)} post(s) ingested from YouTube.")
    else:
        print("YOUTUBE_PLAYLIST_IDS / YOUTUBE_CHANNEL_IDS not configured and none found in GitHub profile — skipping YouTube ingestor.")

    # Split YouTube: channel posts → "My Videos" main section; playlist posts → sidebar panels
    channel_posts = [p for p in watching_posts if p.get("metadata", {}).get("source_type") == "channel"]
    playlist_posts = [p for p in watching_posts if p.get("metadata", {}).get("source_type") == "playlist"]

    # --- Hacker News (My Reading) — HN_USERNAME env var, or auto-discovered from GitHub profile ---
    reading_posts: list[dict] = []
    auto_discovered_hn_username: str | None = None
    effective_hn_usernames = hn_usernames  # start with whatever was explicitly configured

    if not effective_hn_usernames and owner_profile:
        discovered = github_profile.extract_hn_username(owner_profile.social_links)
        if discovered:
            auto_discovered_hn_username = discovered
            effective_hn_usernames = [discovered]
            print(f"  Auto-discovered HN username from GitHub profile: {discovered}")

    if effective_hn_usernames:
        names_str = ", ".join(effective_hn_usernames)
        print(f"Fetching Hacker News (My Reading) for: {names_str}…")
        reading_posts = hackernews.ingest(effective_hn_usernames)
        print(f"  {len(reading_posts)} post(s) ingested from Hacker News.")
    else:
        print("HN_USERNAME not configured and none found in GitHub profile — skipping Hacker News ingestor.")

    # Build active sections (skip sections that produced no posts)
    section_posts = {
        "writing": writing_posts,
        "videos":  channel_posts,
        "watching": playlist_posts,
        "reading": reading_posts,
    }
    active_sections = [
        {**defn, "posts": section_posts[defn["key"]]}
        for defn in _SECTION_DEFS
        if section_posts[defn["key"]]
    ]

    # All posts merged and sorted newest-first (for combined feed / post pages)
    all_posts = sorted(
        writing_posts + channel_posts + playlist_posts + reading_posts,
        key=lambda p: p["created_at"],
        reverse=True,
    )

    # --- Sidebar data ---
    # Split HN posts into stories vs. comments for separate sidebar panels
    _SIDEBAR_LIMIT = 5
    hn_stories = [p for p in reading_posts if p.get("metadata", {}).get("hn_type") == "story"]
    hn_comments = [p for p in reading_posts if p.get("metadata", {}).get("hn_type") == "comment"]
    # Build per-username HN profile links (use first effective username if multiple)
    _hn_user = (effective_hn_usernames or [None])[0]
    hn_submitted_url = (
        f"https://news.ycombinator.com/submitted?id={_hn_user}" if _hn_user else None
    )
    hn_threads_url = (
        f"https://news.ycombinator.com/threads?id={_hn_user}" if _hn_user else None
    )
    hn_profile_url = (
        f"https://news.ycombinator.com/user?id={_hn_user}" if _hn_user else None
    )

    # Build per-playlist sidebar panels (up to _SIDEBAR_LIMIT videos each, in playlist order)
    playlist_groups: list[dict] = []
    _seen_pids: dict[str, dict] = {}
    for p in playlist_posts:
        src_id = p.get("metadata", {}).get("source_id", "")
        if src_id not in _seen_pids:
            grp: dict = {
                "source_id": src_id,
                "view_more_url": p.get("metadata", {}).get("view_more_url", ""),
                "posts": [],
            }
            playlist_groups.append(grp)
            _seen_pids[src_id] = grp
        if len(_seen_pids[src_id]["posts"]) < _SIDEBAR_LIMIT:
            _seen_pids[src_id]["posts"].append(p)

    sidebar = {
        "hn_stories":      hn_stories[:_SIDEBAR_LIMIT],
        "hn_comments":     hn_comments[:_SIDEBAR_LIMIT],
        "hn_submitted_url": hn_submitted_url,
        "hn_threads_url":  hn_threads_url,
        "hn_profile_url":  hn_profile_url,
        "hn_username":     _hn_user,
        "playlist_groups": playlist_groups,
    }

    # --- Jinja2 setup ---
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    base_path = os.environ.get("BASE_PATH", f"/{repo_name}/")
    if not base_path.endswith("/"):
        base_path += "/"

    # Rewrite post_url for every post to use the correct base_path prefix
    for post in all_posts:
        if post["post_url"].startswith("/posts/"):
            post["post_url"] = base_path + post["post_url"].lstrip("/")

    env.globals["repo_url"] = repo_url
    env.globals["repo_name"] = repo_name
    env.globals["base_path"] = base_path
    env.globals["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    env.globals["generated_in"] = f"{time.monotonic() - _start:.1f}s"
    env.globals["has_social_links"] = bool(owner_profile and owner_profile.social_links)

    # Pipeline run URL — available when running inside GitHub Actions
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if run_id:
        env.globals["pipeline_url"] = f"https://github.com/{repo}/actions/runs/{run_id}"
    else:
        env.globals["pipeline_url"] = f"https://github.com/{repo}/actions"

    # Pre-compute labels so templates can link to /labels/{slug}/ pages
    label_map: dict[str, list[dict]] = {}
    for post in writing_posts:
        for lbl in post.get("labels", []):
            label_map.setdefault(lbl, []).append(post)
    all_labels = sorted(
        [{"name": lbl, "slug": label_slug(lbl), "count": len(posts)}
         for lbl, posts in label_map.items()],
        key=lambda x: (-x["count"], x["name"]),
    )
    env.globals["all_labels"] = all_labels
    env.globals["label_slug"] = label_slug

    output_dir.mkdir(parents=True, exist_ok=True)

    # Render index page
    index_tmpl = env.get_template("index.html")
    index_html = index_tmpl.render(sections=active_sections, sidebar=sidebar)
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    print("Wrote index.html")

    # Render individual post pages
    post_tmpl = env.get_template("post.html")
    for post in all_posts:
        post_dir = output_dir / "posts" / post["post_id"]
        post_dir.mkdir(parents=True, exist_ok=True)
        post_html = post_tmpl.render(post=post)
        (post_dir / "index.html").write_text(post_html, encoding="utf-8")
        print(f"  Wrote posts/{post['post_id']}/index.html")

    copy_static(output_dir)
    print("Copied static assets.")

    # Render per-label pages
    if label_map:
        label_tmpl = env.get_template("label.html")
        for lbl, lbl_posts in label_map.items():
            slug = label_slug(lbl)
            label_dir = output_dir / "labels" / slug
            label_dir.mkdir(parents=True, exist_ok=True)
            label_html = label_tmpl.render(label_name=lbl, current_label_slug=slug, posts=lbl_posts)
            (label_dir / "index.html").write_text(label_html, encoding="utf-8")
            print(f"  Wrote labels/{slug}/index.html ({len(lbl_posts)} post(s))")

    # Render config page
    config_ctx = {
        "hn_usernames": effective_hn_usernames or [],
        "auto_discovered_hn_username": auto_discovered_hn_username,
        "playlist_ids": playlist_ids,
        "channel_ids": channel_ids,
        "auto_discovered_channels": auto_discovered_channels,
        "owner_profile": owner_profile,
        "hidden_labels": sorted(hidden_labels),
        "blocked_user_count": len(blocked_users),
        "writing_post_count": len(writing_posts),
        "video_post_count": len(channel_posts),
        "playlist_post_count": len(playlist_posts),
        "reading_post_count": len(reading_posts),
    }
    config_tmpl = env.get_template("config.html")
    config_html = config_tmpl.render(**config_ctx)
    config_page_dir = output_dir / "config"
    config_page_dir.mkdir(parents=True, exist_ok=True)
    (config_page_dir / "index.html").write_text(config_html, encoding="utf-8")
    print("Wrote config/index.html")

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

    youtube_playlist_ids = os.environ.get("YOUTUBE_PLAYLIST_IDS") or None
    youtube_channel_ids = os.environ.get("YOUTUBE_CHANNEL_IDS") or None

    # HN usernames: from HN_USERNAME env var and/or local config file (gitignored)
    hn_usernames = hackernews.load_usernames(os.environ.get("HN_USERNAME") or None)

    generate_site(
        repo=repo,
        token=token,
        output_dir=output_dir,
        youtube_playlist_ids=youtube_playlist_ids,
        youtube_channel_ids=youtube_channel_ids,
        hn_usernames=hn_usernames or None,
    )


if __name__ == "__main__":
    main()
