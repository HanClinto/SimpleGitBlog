"""Ingestor package for SimpleGitBlog.

Each ingestor module exposes an ``ingest()`` function that returns a list of
post dicts conforming to the common schema:

    post_id        str   Stable unique identifier, e.g. "gh-42", "yt-abc", "hn-123"
    title          str   Post title
    post_url       str   Relative URL to the local post page, e.g. "/posts/gh-42/"
    source_url     str   Canonical external URL for the original content
    author         str   Display name of the author / channel / submitter
    author_url     str   URL to the author's profile
    avatar_url     str|None  Avatar image URL, or None
    created_at     str   ISO 8601 datetime string (used for sorting)
    created_at_fmt str   Human-readable date, e.g. "April 13, 2026"
    created_at_iso str   ISO datetime for <time datetime="…"> attribute
    body_html      str   Sanitized HTML body
    excerpt        str   Plain-text excerpt (~280 chars)
    source         str   Source identifier: "github" | "youtube" | "hackernews"
    section        str   Section key: "writing" | "watching" | "reading"
    labels         list  Label strings (GitHub only; empty list for other sources)
    comment_count  int   Number of blog comments (GitHub only; 0 for other sources)
    comments       list  Comment dicts (GitHub only; empty list for other sources)
    metadata       dict  Source-specific extra data
"""
