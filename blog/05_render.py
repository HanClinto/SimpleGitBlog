"""
Stage 05 — Render static HTML and write output to _site/.

Reads:  _cache/profile.json
        _cache/issues.json
        _cache/youtube.json
        _cache/hn.json
Writes: _site/   (full static site, ready to upload as a GitHub Pages artifact)

Environment variables consumed:
  GITHUB_REPOSITORY   required  owner/repo
  GITHUB_RUN_ID       optional  injected by GitHub Actions for pipeline links
  OUTPUT_DIR          optional  output directory (default: _site)
  BASE_PATH           optional  URL base path (default: /{repo_name}/)
  FEED_SOURCES        optional  comma/newline-separated source keys for the main feed
  BLOG_CONFIGURED     optional  set by the Set-Up workflow; suppresses first-run warning
"""

import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.parse
from html import escape as html_escape
from html.parser import HTMLParser
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from blog.ingestors import github_issues  # noqa: E402
from blog.ingestors.github_profile import OwnerProfile, SocialLink  # noqa: E402
from blog.pipeline_cache import read_cache  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR   = Path(__file__).parent.resolve()
_TEMPLATES_DIR = _SCRIPT_DIR / "templates"
_STATIC_DIR    = _SCRIPT_DIR / "static"
_CONFIG_DIR    = _SCRIPT_DIR.parent / "config"

# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

_PAGE_SIZE = 10

# ---------------------------------------------------------------------------
# Content routing
# ---------------------------------------------------------------------------

_ALL_SOURCES = ("writing", "channel", "playlists", "hn_stories", "hn_comments")

_DEFAULT_FEED_SOURCES: frozenset[str] = frozenset({"writing", "channel", "hn_stories"})

_SOURCE_META: dict[str, dict] = {
    "writing":     {"title": "My Writing",     "icon": "✍️"},
    "channel":     {"title": "My Videos",      "icon": "🎥"},
    "playlists":   {"title": "My Watching",    "icon": "📺"},
    "hn_stories":  {"title": "HN Submissions", "icon": "🗞️"},
    "hn_comments": {"title": "HN Comments",   "icon": "💬"},
}


def _parse_feed_sources(env_val: str | None) -> frozenset[str]:
    if not env_val:
        return _DEFAULT_FEED_SOURCES
    parsed = frozenset(
        tok
        for raw in re.split(r"[\r\n,]+", env_val)
        for tok in [raw.strip().lower()]
        if tok in _ALL_SOURCES
    )
    return parsed if parsed else _DEFAULT_FEED_SOURCES


# ---------------------------------------------------------------------------
# Static asset helpers
# ---------------------------------------------------------------------------

def _copy_static(output_dir: Path) -> None:
    static_out = output_dir / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(_STATIC_DIR, static_out)


def _write_nojekyll(output_dir: Path) -> None:
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")


def _label_slug(label: str) -> str:
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or urllib.parse.quote(label, safe="")


# ---------------------------------------------------------------------------
# Post link helpers
# ---------------------------------------------------------------------------

_ISSUE_REF_RE = re.compile(r"(?<![\w&])#(\d+)\b")


def _github_issue_number_from_url(url: str, repo: str) -> int | None:
    """Return an issue number for same-repo GitHub issue URLs, otherwise None."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() != "github.com":
        return None
    path_parts = [p for p in parsed.path.split("/") if p]
    repo_parts = repo.split("/", 1)
    if len(path_parts) < 4 or len(repo_parts) != 2:
        return None
    if path_parts[0].lower() != repo_parts[0].lower() or path_parts[1].lower() != repo_parts[1].lower():
        return None
    if path_parts[2] != "issues":
        return None
    try:
        return int(path_parts[3])
    except ValueError:
        return None


class _IssueLinkRewriter(HTMLParser):
    """Rewrite same-repo issue links and plain #123 refs inside sanitized HTML."""

    _SKIP_TEXT_TAGS = {"a", "code", "pre"}

    def __init__(self, issue_url_map: dict[int, str], repo: str):
        super().__init__(convert_charrefs=False)
        self.issue_url_map = issue_url_map
        self.repo = repo
        self.parts: list[str] = []
        self._skip_text_depth = 0

    def _rewrite_href(self, href: str) -> str:
        issue_number = _github_issue_number_from_url(href, self.repo)
        if issue_number is None:
            return href
        local_url = self.issue_url_map.get(issue_number)
        if not local_url:
            return href
        parsed = urllib.parse.urlparse(href)
        suffix = f"#{parsed.fragment}" if parsed.fragment else ""
        return local_url + suffix

    def _format_start_tag(self, tag: str, attrs: list[tuple[str, str | None]], closed: bool = False) -> str:
        attr_parts = []
        for name, value in attrs:
            if tag == "a" and name == "href" and value is not None:
                value = self._rewrite_href(value)
            if value is None:
                attr_parts.append(name)
            else:
                attr_parts.append(f'{name}="{html_escape(value, quote=True)}"')
        attrs_str = (" " + " ".join(attr_parts)) if attr_parts else ""
        return f"<{tag}{attrs_str}{' /' if closed else ''}>"

    def _link_issue_refs(self, text: str) -> str:
        def repl(match: re.Match) -> str:
            issue_number = int(match.group(1))
            local_url = self.issue_url_map.get(issue_number)
            if not local_url:
                return html_escape(match.group(0), quote=False)
            ref_text = match.group(0)
            href = html_escape(local_url, quote=True)
            label = html_escape(ref_text)
            return f'<a href="{href}" rel="nofollow noopener noreferrer">{label}</a>'

        result: list[str] = []
        last = 0
        for match in _ISSUE_REF_RE.finditer(text):
            result.append(html_escape(text[last:match.start()], quote=False))
            result.append(repl(match))
            last = match.end()
        result.append(html_escape(text[last:], quote=False))
        return "".join(result)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TEXT_TAGS:
            self._skip_text_depth += 1
        self.parts.append(self._format_start_tag(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self._format_start_tag(tag, attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if tag in self._SKIP_TEXT_TAGS and self._skip_text_depth:
            self._skip_text_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_text_depth:
            self.parts.append(html_escape(data, quote=False))
        else:
            self.parts.append(self._link_issue_refs(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")


def _rewrite_issue_links(html: str, issue_url_map: dict[int, str], repo: str) -> str:
    if not html or not issue_url_map:
        return html
    parser = _IssueLinkRewriter(issue_url_map, repo)
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def _series_info_from_title(title: str) -> tuple[str, int] | None:
    part_match = re.match(r"^(.+?)\s+Part\s+(\d+)\s*:", title, flags=re.IGNORECASE)
    if part_match:
        return part_match.group(1).strip(), int(part_match.group(2))
    return None


def _attach_series_metadata(posts: list[dict]) -> None:
    """Attach generated previous/next + table-of-contents metadata for obvious post series."""
    series_by_name: dict[str, list[dict]] = {}
    for post in posts:
        info = _series_info_from_title(post.get("title", ""))
        if not info:
            continue
        series_name, part = info
        post["_series_part"] = part
        series_by_name.setdefault(series_name, []).append(post)

    # Add an introductory post as Part 1 when it uses "SeriesName: ..." and no explicit Part 1 exists.
    for series_name, series_posts in list(series_by_name.items()):
        existing_parts = {p["_series_part"] for p in series_posts}
        if 1 in existing_parts:
            continue
        intro_prefix = f"{series_name}:"
        intro_candidates = [
            post for post in posts
            if post.get("title", "").startswith(intro_prefix) and "_series_part" not in post
        ]
        if intro_candidates:
            # There may be later follow-up posts that share the same title prefix
            # (for example "CollectorVision: Card Accuracy vs. Edition Accuracy").
            # Treat the earliest matching post as the actual series intro.
            intro = sorted(
                intro_candidates,
                key=lambda p: (p.get("created_at", ""), p.get("metadata", {}).get("number", 0)),
            )[0]
            intro["_series_part"] = 1
            series_posts.append(intro)

    for series_name, series_posts in series_by_name.items():
        if len(series_posts) < 2:
            continue
        ordered = sorted(series_posts, key=lambda p: (p["_series_part"], p.get("created_at", "")))
        toc = [
            {
                "part": p["_series_part"],
                "title": p.get("title", ""),
                "url": p.get("post_url", ""),
            }
            for p in ordered
        ]
        for index, post in enumerate(ordered):
            post["series"] = {
                "name": series_name,
                "part": post["_series_part"],
                "posts": [dict(item, is_current=(item["url"] == post.get("post_url", ""))) for item in toc],
                "prev": toc[index - 1] if index > 0 else None,
                "next": toc[index + 1] if index + 1 < len(toc) else None,
            }


# ---------------------------------------------------------------------------
# Profile reconstruction
# ---------------------------------------------------------------------------

def _reconstruct_profile(data: dict | None) -> OwnerProfile | None:
    """Rebuild an OwnerProfile NamedTuple from the cached dict."""
    if not data:
        return None
    return OwnerProfile(
        login=data.get("login", ""),
        name=data.get("name"),
        bio=data.get("bio"),
        avatar_url=data.get("avatar_url"),
        website=data.get("website"),
        twitter_username=data.get("twitter_username"),
        social_links=[
            SocialLink(provider=s["provider"], url=s["url"])
            for s in data.get("social_links", [])
        ],
    )


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(
    repo: str,
    output_dir: Path,
    feed_sources: frozenset[str],
    run_id: str,
    base_path: str,
) -> None:
    render_start = time.monotonic()

    repo_owner = repo.split("/")[0]
    repo_name  = repo.split("/")[-1]
    repo_url   = f"https://github.com/{repo}"

    # --- Load all stage caches ---
    profile_cache = read_cache("profile")
    issues_cache  = read_cache("issues")
    youtube_cache = read_cache("youtube")
    hn_cache      = read_cache("hn")

    # --- Reconstruct typed objects ---
    owner_profile = _reconstruct_profile(profile_cache.get("data"))

    writing_posts: list[dict] = issues_cache.get("posts", [])
    watching_posts: list[dict] = youtube_cache.get("posts", [])
    reading_posts: list[dict]  = hn_cache.get("posts", [])

    channel_posts  = [p for p in watching_posts if p.get("metadata", {}).get("source_type") == "channel"]
    playlist_posts = [p for p in watching_posts if p.get("metadata", {}).get("source_type") == "playlist"]

    # Config-page metadata from caches
    playlist_ids           = youtube_cache.get("playlist_ids", [])
    channel_ids            = youtube_cache.get("channel_ids", [])
    auto_discovered_channels: list[str] = (
        youtube_cache.get("auto_discovered_channels", [])
        or profile_cache.get("auto_discovered_channels", [])
    )
    effective_hn_usernames: list[str]   = hn_cache.get("effective_hn_usernames", [])
    auto_discovered_hn_username: str | None = hn_cache.get("auto_discovered_hn_username")

    # Load config files for the config page
    hidden_labels = github_issues._load_hidden_labels(_CONFIG_DIR)
    blocked_users = github_issues._load_blocked_users(_CONFIG_DIR)

    # --- Collect pipeline stage summaries for the footer ---
    _stage_labels = {
        "profile": "GitHub Profile",
        "issues":  "GitHub Issues",
        "youtube": "YouTube",
        "hn":      "Hacker News",
    }
    pipeline_stages: list[dict] = []
    all_warnings: list[str] = []
    for key in ("profile", "issues", "youtube", "hn"):
        cache = {"profile": profile_cache, "issues": issues_cache,
                 "youtube": youtube_cache, "hn": hn_cache}[key]
        stage_warnings = cache.get("warnings", [])
        all_warnings.extend(stage_warnings)
        pipeline_stages.append({
            "name":     _stage_labels[key],
            "elapsed":  cache.get("elapsed", 0.0),
            "warnings": stage_warnings,
            "skipped":  cache.get("skipped", False),
        })

    # --- All posts merged ---
    all_posts = sorted(
        writing_posts + channel_posts + playlist_posts + reading_posts,
        key=lambda p: p["created_at"],
        reverse=True,
    )

    # Inject synthetic source-type labels
    for post in all_posts:
        src = post.get("source")
        if src == "github":
            synthetic = "Blog Post"
        elif src == "youtube":
            synthetic = "Video"
        elif src == "hackernews":
            hn_type = post.get("metadata", {}).get("hn_type", "story")
            synthetic = "Link Submission" if hn_type == "story" else "HN Comment"
        else:
            continue
        if synthetic not in post["labels"]:
            post["labels"].append(synthetic)

    # --- Sidebar data ---
    _SIDEBAR_LIMIT = 5
    hn_stories  = [p for p in reading_posts if p.get("metadata", {}).get("hn_type") == "story"]
    hn_comments = [p for p in reading_posts if p.get("metadata", {}).get("hn_type") == "comment"]

    _hn_user = (effective_hn_usernames or [None])[0]
    hn_submitted_url = f"https://news.ycombinator.com/submitted?id={_hn_user}" if _hn_user else None
    hn_threads_url   = f"https://news.ycombinator.com/threads?id={_hn_user}"   if _hn_user else None
    hn_profile_url   = f"https://news.ycombinator.com/user?id={_hn_user}"      if _hn_user else None

    _source_posts: dict[str, list[dict]] = {
        "writing":     writing_posts,
        "channel":     channel_posts,
        "playlists":   playlist_posts,
        "hn_stories":  hn_stories,
        "hn_comments": hn_comments,
    }

    # --- Main feed ---
    feed_posts = sorted(
        [p for key in _ALL_SOURCES if key in feed_sources for p in _source_posts[key]],
        key=lambda p: p["created_at"],
        reverse=True,
    )
    total_pages = max(1, (len(feed_posts) + _PAGE_SIZE - 1) // _PAGE_SIZE)

    # --- Sidebar panels ---
    sidebar_panels: list[dict] = []
    for key in _ALL_SOURCES:
        if key in feed_sources:
            continue
        meta = _SOURCE_META[key]
        if key == "playlists":
            seen: dict[str, dict] = {}
            grp_list: list[dict] = []
            for p in playlist_posts:
                src_id = p.get("metadata", {}).get("source_id", "")
                if src_id not in seen:
                    grp: dict = {
                        "type": "playlist",
                        "title": meta["title"],
                        "icon": meta["icon"],
                        "posts": [],
                        "view_all_url": p.get("metadata", {}).get("view_more_url", ""),
                    }
                    grp_list.append(grp)
                    seen[src_id] = grp
                if len(seen[src_id]["posts"]) < _SIDEBAR_LIMIT:
                    seen[src_id]["posts"].append(p)
            # When multiple playlists are present, number the sidebar headings
            # so readers can distinguish them ("My Watching 1", "My Watching 2", …)
            if len(grp_list) > 1:
                for i, grp in enumerate(grp_list, 1):
                    grp["title"] = f"{meta['title']} {i}"
            sidebar_panels.extend(grp_list)
        else:
            view_all = {
                "writing":     f"{repo_url}/issues",
                "channel":     None,
                "hn_stories":  hn_submitted_url,
                "hn_comments": hn_threads_url,
            }.get(key)
            sidebar_panels.append({
                "type":         key,
                "title":        meta["title"],
                "icon":         meta["icon"],
                "posts":        _source_posts[key][:_SIDEBAR_LIMIT],
                "view_all_url": view_all,
                "hn_threads_url":  hn_threads_url,
                "hn_profile_url":  hn_profile_url,
            })

    # --- Jinja2 setup ---
    jinja_env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )

    # Rewrite post_url for every post to use the correct base_path prefix
    for post in all_posts:
        if post["post_url"].startswith("/posts/"):
            post["post_url"] = base_path + post["post_url"].lstrip("/")

    issue_url_map = {
        int(post["metadata"]["number"]): post["post_url"]
        for post in all_posts
        if post.get("source") == "github" and post.get("metadata", {}).get("number") is not None
    }
    for post in all_posts:
        post["body_html"] = _rewrite_issue_links(post.get("body_html", ""), issue_url_map, repo)
        for comment in post.get("comments", []):
            comment["body_html"] = _rewrite_issue_links(comment.get("body_html", ""), issue_url_map, repo)

    _attach_series_metadata(all_posts)

    total_elapsed = sum(s["elapsed"] for s in pipeline_stages)
    jinja_env.globals.update({
        "repo_url":         repo_url,
        "repo_name":        repo_name,
        "base_path":        base_path,
        "generated_at":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "generated_in":     f"{total_elapsed:.1f}s",
        "has_social_links": bool(owner_profile and owner_profile.social_links),
        "pipeline_url":     (
            f"https://github.com/{repo}/actions/runs/{run_id}"
            if run_id else
            f"https://github.com/{repo}/actions"
        ),
        "pipeline_stages":   pipeline_stages,
        "pipeline_warnings": all_warnings,
    })

    # Pre-compute labels
    label_map: dict[str, list[dict]] = {}
    for post in all_posts:
        for lbl in post.get("labels", []):
            label_map.setdefault(lbl, []).append(post)
    all_labels = sorted(
        [{"name": lbl, "slug": _label_slug(lbl), "count": len(posts)}
         for lbl, posts in label_map.items()],
        key=lambda x: (-x["count"], x["name"]),
    )
    jinja_env.globals["all_labels"]   = all_labels
    jinja_env.globals["label_slug"]   = _label_slug

    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Paginated index pages ---
    index_tmpl = jinja_env.get_template("index.html")
    for page_num in range(1, total_pages + 1):
        start = (page_num - 1) * _PAGE_SIZE
        page_posts = feed_posts[start : start + _PAGE_SIZE]
        prev_url = (
            base_path if page_num == 2
            else f"{base_path}page/{page_num - 1}/"
        ) if page_num > 1 else None
        next_url = f"{base_path}page/{page_num + 1}/" if page_num < total_pages else None
        page_html = index_tmpl.render(
            feed_posts=page_posts,
            sidebar_panels=sidebar_panels,
            page_num=page_num,
            total_pages=total_pages,
            prev_url=prev_url,
            next_url=next_url,
        )
        if page_num == 1:
            (output_dir / "index.html").write_text(page_html, encoding="utf-8")
            print("Wrote index.html")
        else:
            page_dir = output_dir / "page" / str(page_num)
            page_dir.mkdir(parents=True, exist_ok=True)
            (page_dir / "index.html").write_text(page_html, encoding="utf-8")
            print(f"Wrote page/{page_num}/index.html")

    # --- Individual post pages ---
    post_tmpl = jinja_env.get_template("post.html")
    for post in all_posts:
        post_dir = output_dir / "posts" / post["post_id"]
        post_dir.mkdir(parents=True, exist_ok=True)
        (post_dir / "index.html").write_text(post_tmpl.render(post=post), encoding="utf-8")
        print(f"  Wrote posts/{post['post_id']}/index.html")

    _copy_static(output_dir)
    print("Copied static assets.")

    # --- Per-label pages ---
    if label_map:
        label_tmpl = jinja_env.get_template("label.html")
        for lbl, lbl_posts in label_map.items():
            slug = _label_slug(lbl)
            label_dir = output_dir / "labels" / slug
            label_dir.mkdir(parents=True, exist_ok=True)
            label_html = label_tmpl.render(
                label_name=lbl, current_label_slug=slug, posts=lbl_posts
            )
            (label_dir / "index.html").write_text(label_html, encoding="utf-8")
            print(f"  Wrote labels/{slug}/index.html ({len(lbl_posts)} post(s))")

    # --- Config page ---
    config_tmpl = jinja_env.get_template("config.html")
    config_html = config_tmpl.render(
        hn_usernames=effective_hn_usernames,
        auto_discovered_hn_username=auto_discovered_hn_username,
        playlist_ids=playlist_ids,
        channel_ids=channel_ids,
        auto_discovered_channels=auto_discovered_channels,
        owner_profile=owner_profile,
        hidden_labels=sorted(hidden_labels),
        blocked_user_count=len(blocked_users),
        writing_post_count=len(writing_posts),
        video_post_count=len(channel_posts),
        playlist_post_count=len(playlist_posts),
        reading_post_count=len(reading_posts),
        feed_sources=list(feed_sources),
        pipeline_stages=pipeline_stages,
    )
    config_page_dir = output_dir / "config"
    config_page_dir.mkdir(parents=True, exist_ok=True)
    (config_page_dir / "index.html").write_text(config_html, encoding="utf-8")
    print("Wrote config/index.html")

    _write_nojekyll(output_dir)
    print("Wrote .nojekyll")

    render_elapsed = time.monotonic() - render_start
    total_with_render = total_elapsed + render_elapsed
    warn_count = len(all_warnings)
    print(
        f"\nDone! Site generated in {output_dir}/ "
        f"(fetch: {total_elapsed:.1f}s, render: {render_elapsed:.1f}s, "
        f"total: {total_with_render:.1f}s, {warn_count} warning(s))"
    )
    if warn_count:
        print(f"\n{'='*60}")
        print(f"  {warn_count} WARNING(S) during content fetch:")
        for i, w in enumerate(all_warnings, 1):
            print(f"  {i}. {w}")
        print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("ERROR: GITHUB_REPOSITORY is not set.", file=sys.stderr)
        sys.exit(1)

    output_dir  = Path(os.environ.get("OUTPUT_DIR", "_site")).resolve()
    run_id      = os.environ.get("GITHUB_RUN_ID", "").strip()
    feed_sources = _parse_feed_sources(os.environ.get("FEED_SOURCES") or None)

    repo_name = repo.split("/")[-1]
    base_path = os.environ.get("BASE_PATH", f"/{repo_name}/")
    if not base_path.endswith("/"):
        base_path += "/"

    render(
        repo=repo,
        output_dir=output_dir,
        feed_sources=feed_sources,
        run_id=run_id,
        base_path=base_path,
    )


if __name__ == "__main__":
    main()
