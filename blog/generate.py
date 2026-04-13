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

    YOUTUBE_API_KEY      Optional. YouTube Data API v3 key.
                         Required to enable the "My Watching" section.
    YOUTUBE_PLAYLIST_IDS Optional. Comma-separated YouTube playlist IDs.
                         Set this as a GitHub Actions repository variable
                         (Settings → Variables) so it is NOT stored in source.

    HN_USERNAME          Optional. Hacker News username.
                         Required to enable the "My Reading" section.
                         Set this as a GitHub Actions repository variable
                         (Settings → Variables) so it is NOT stored in source.
"""

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path so package imports work when this
# script is invoked directly (python blog/generate.py) from the repo root.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from blog.ingestors import github_issues, hackernews, youtube  # noqa: E402

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
    {"key": "writing",    "title": "My Writing",  "icon": "✍️"},
    {"key": "watching",   "title": "My Watching", "icon": "📺"},
    {"key": "reading",    "title": "My Reading",  "icon": "📰"},
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


# ---------------------------------------------------------------------------
# Site generation
# ---------------------------------------------------------------------------


def generate_site(
    repo: str,
    token: str | None,
    output_dir: Path,
    youtube_api_key: str | None = None,
    youtube_playlist_ids: str | None = None,
    hn_usernames: list[str] | None = None,
) -> None:
    repo_name = repo.split("/")[-1]
    repo_url = f"https://github.com/{repo}"

    # --- GitHub Issues (My Writing) — always runs ---
    print("Fetching GitHub Issues (My Writing)…")
    writing_posts = github_issues.ingest(repo, token, CONFIG_DIR)
    print(f"  {len(writing_posts)} post(s) ingested from GitHub Issues.")

    # --- YouTube playlists (My Watching) — requires YOUTUBE_API_KEY ---
    watching_posts: list[dict] = []
    if youtube_api_key:
        print("Fetching YouTube playlists (My Watching)…")
        watching_posts = youtube.ingest(youtube_api_key, CONFIG_DIR, youtube_playlist_ids)
        print(f"  {len(watching_posts)} post(s) ingested from YouTube.")
    else:
        print("YOUTUBE_API_KEY not set — skipping YouTube ingestor.")

    # --- Hacker News (My Reading) — requires HN_USERNAME ---
    reading_posts: list[dict] = []
    if hn_usernames:
        names_str = ", ".join(hn_usernames)
        print(f"Fetching Hacker News (My Reading) for: {names_str}…")
        reading_posts = hackernews.ingest(hn_usernames)
        print(f"  {len(reading_posts)} post(s) ingested from Hacker News.")
    else:
        print("HN_USERNAME not configured — skipping Hacker News ingestor.")

    # Build active sections (skip sections that produced no posts)
    section_posts = {
        "writing":  writing_posts,
        "watching": watching_posts,
        "reading":  reading_posts,
    }
    active_sections = [
        {**defn, "posts": section_posts[defn["key"]]}
        for defn in _SECTION_DEFS
        if section_posts[defn["key"]]
    ]

    # All posts merged and sorted newest-first (for combined feed / post pages)
    all_posts = sorted(
        writing_posts + watching_posts + reading_posts,
        key=lambda p: p["created_at"],
        reverse=True,
    )

    # --- Jinja2 setup ---
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
    index_html = index_tmpl.render(sections=active_sections)
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

    youtube_api_key = os.environ.get("YOUTUBE_API_KEY") or None
    youtube_playlist_ids = os.environ.get("YOUTUBE_PLAYLIST_IDS") or None

    # HN usernames: from HN_USERNAME env var and/or local config file (gitignored)
    hn_usernames = hackernews.load_usernames(CONFIG_DIR, os.environ.get("HN_USERNAME") or None)

    generate_site(
        repo=repo,
        token=token,
        output_dir=output_dir,
        youtube_api_key=youtube_api_key,
        youtube_playlist_ids=youtube_playlist_ids,
        hn_usernames=hn_usernames or None,
    )


if __name__ == "__main__":
    main()
