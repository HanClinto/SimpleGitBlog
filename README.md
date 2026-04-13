# SimpleGitBlog

A static blog engine that pulls content from multiple sources and publishes them to GitHub Pages — no CMS, no database, no complex infrastructure.

Content is organised into thematic sections:

| Section | Source | Label |
|---------|--------|-------|
| ✍️ **My Writing** | GitHub Issues labelled `blog-post` | always on |
| 📺 **My Watching** | YouTube playlist videos | set `YOUTUBE_PLAYLIST_IDS` (no API key needed) |
| 📰 **My Reading** | Hacker News submissions & comments | set `HN_USERNAME` |

---

## ✨ Features

- **WYSIWYG authoring** — use GitHub's built-in Issue editor (Markdown, images, code blocks, mentions)
- **Comment threads** — Issue comments become blog comment threads
- **YouTube embeds** — playlist videos render as responsive embedded players; paste any YouTube URL on its own line in a post to auto-embed it (no API key needed)
- **HN activity** — your Hacker News stories and comments appear automatically
- **XSS-safe** — all user content is sanitised with `bleach` before rendering
- **Mobile-friendly** — clean, responsive CSS with no JavaScript dependencies
- **Block spammers** — add usernames to `config/blocked_users.txt`
- **Cross-link forks** — commenters who have forked your repo get a "Their blog" link automatically
- **Static & fast** — just HTML/CSS, hosted on GitHub Pages for free

---

## 🚀 Quick Start

### 1. Fork this repository

Click **Fork** on the GitHub repository page. Your fork becomes your personal blog.

### 2. Enable GitHub Pages

Go to **Settings → Pages** and set the source to the `gh-pages` branch (it will be created automatically after the first build).

### 3. Enable Actions write permissions

Go to **Settings → Actions → General → Workflow permissions** and select **Read and write permissions**.

### 4. Publish your first post

1. Open a new Issue in your forked repository.
2. Give it a descriptive **title** — this becomes your post title.
3. Write your content in the body using Markdown.
4. Add the label **`blog-post`** to the issue.
5. The GitHub Action will trigger automatically and rebuild your site within a minute or two.

Your post will be live at `https://<your-username>.github.io/<repo-name>/`.

---

## ⚙️ Configuration

### My Writing — GitHub Issues

By default, only the **repository owner** can publish blog posts. Issues opened by other users with the `blog-post` label are silently skipped.

To grant additional users posting rights, add their GitHub usernames to `config/allowed_posters.txt`:

```
# config/allowed_posters.txt
alice
bob
```

To allow **anyone** to publish (open-contributor mode), add a `*` line. The repository owner is always implicitly allowed regardless of the file contents.

To block commenters, add their usernames to `config/blocked_users.txt`.

---

### My Watching — YouTube Playlists

**No API key required!** The ingestor uses YouTube's public Atom/RSS feeds, which work for any public playlist without registration or credentials.

**Setup (recommended — keeps your playlist IDs out of source control):**

In your GitHub repo, go to **Settings → Secrets and variables → Actions → Variables** and add a **Variable** named `YOUTUBE_PLAYLIST_IDS` with your playlist ID(s), comma-separated.

To find a playlist ID, open the playlist on YouTube and copy the `list=` parameter:
```
https://www.youtube.com/playlist?list=PLILJALPUFXDmE84sBSVlGqcaDFRJ2RZ5Q
                                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                       this is the playlist ID
```

> **Note:** The RSS feed returns up to the 15 most-recently-added videos per playlist. Add multiple playlist IDs (e.g. one per year) if you need more history.

**Local development only:** Copy `config/youtube_playlists.txt.example` to `config/youtube_playlists.txt` and add your IDs. That file is gitignored and will never be committed.

**Auto-embedding YouTube links in blog posts:** You don't even need a playlist configured to get YouTube embeds. Simply paste a YouTube video URL on its own line in any GitHub Issue (blog post):

```
Check out this great talk:

https://www.youtube.com/watch?v=dQw4w9WgXcQ

It covers...
```

The video will automatically render as a responsive embedded player — no configuration needed.

---

### My Reading — Hacker News

No API key needed — the [Algolia HN Search API](https://hn.algolia.com/api/v1) is public.

**Setup (recommended — keeps your username out of source control):**

In your GitHub repo, go to **Settings → Secrets and variables → Actions → Variables** and add a **Variable** named `HN_USERNAME` with your HN username.

**Local development only:** Copy `config/hackernews.txt.example` to `config/hackernews.txt` and add your username. That file is gitignored and will never be committed.

> **Why not commit the username?** If someone forks your blog, you don't want your personal HN activity appearing on their site by default. Storing configuration in GitHub Actions variables means each fork has its own clean settings.

---

### Custom domain

Set the `cname` field in `.github/workflows/build-blog.yml`:

```yaml
- name: Deploy to GitHub Pages
  uses: peaceiris/actions-gh-pages@v4
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    publish_dir: ./_site
    cname: blog.example.com   # ← your domain here
```

### Rebuild schedule

The blog rebuilds every 6 hours via a cron schedule, on every Issue or comment event, and can be triggered manually from the **Actions** tab.

---

## 🏗️ How It Works

```
GitHub Issues ──────────────────────────────────────────┐
YouTube Playlists (YOUTUBE_PLAYLIST_IDS, no API key)       ├──► blog/generate.py ──► _site/ ──► GitHub Pages
Hacker News (HN_USERNAME) ──────────────────────────────┘
```

Each source is handled by an *ingestor* in `blog/ingestors/`. Every ingestor produces posts in a common schema, so the site generator and templates are source-agnostic.

### Key files

| Path | Purpose |
|------|---------|
| `blog/generate.py` | Orchestrator — calls all ingestors and renders the site |
| `blog/utils.py` | Shared utilities (HTML sanitisation, Markdown, date formatting) |
| `blog/ingestors/github_issues.py` | ✍️ My Writing ingestor |
| `blog/ingestors/youtube.py` | 📺 My Watching ingestor |
| `blog/ingestors/hackernews.py` | 📰 My Reading ingestor |
| `blog/templates/` | Jinja2 HTML templates |
| `blog/static/` | CSS and favicon |
| `config/allowed_posters.txt` | GitHub usernames allowed to author blog posts |
| `config/blocked_users.txt` | Blocked commenter usernames |
| `config/youtube_playlists.txt.example` | Template for local YouTube config |
| `config/hackernews.txt.example` | Template for local HN config |
| `.github/workflows/build-blog.yml` | CI/CD pipeline |
| `requirements.txt` | Python dependencies |

---

## 🛡️ Security

- All content rendered from Issues, YouTube descriptions, and HN comments passes through `bleach.clean()` with a strict tag/attribute allowlist.
- `javascript:` and `data:` URI schemes are stripped from all `href` and `src` attributes.
- All links in user content receive `rel="nofollow noopener noreferrer"`.
- Personal configuration (HN username, YouTube playlist IDs) is stored in GitHub Actions repository variables — never in committed source files — so forks start with a clean slate.

---

## Credits

Built with:
- [python-markdown](https://python-markdown.github.io/)
- [bleach](https://bleach.readthedocs.io/)
- [Jinja2](https://jinja.palletsprojects.com/)
- [requests](https://requests.readthedocs.io/)
- [peaceiris/actions-gh-pages](https://github.com/peaceiris/actions-gh-pages)
- [Algolia HN Search API](https://hn.algolia.com/api/v1) (Hacker News)
- [YouTube Atom/RSS feeds](https://www.youtube.com/feeds/videos.xml) (YouTube, no API key)
