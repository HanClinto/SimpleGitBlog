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
    reactions      list  Reaction dicts [{emoji, label, count}] (GitHub only; empty for others)
    comment_count  int   Number of blog comments (GitHub only; 0 for other sources)
    comments       list  Comment dicts (GitHub only; empty list for other sources)
    metadata       dict  Source-specific extra data

Each comment dict in ``comments`` has:
    id             int   GitHub comment ID
    author         str   GitHub login
    author_url     str   URL to the author's GitHub profile
    avatar_url     str   Avatar image URL
    created_at     str   ISO 8601 datetime string
    created_at_fmt str   Human-readable date
    created_at_iso str   ISO datetime for <time datetime="…"> attribute
    comment_url    str   Direct URL to this comment on GitHub
    body_html      str   Sanitized HTML body
    reactions      list  Reaction dicts [{emoji, label, count}]
    fork_blog_url  str|None  GitHub Pages URL of the commenter's blog fork, if any
"""
