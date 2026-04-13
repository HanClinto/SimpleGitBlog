# SimpleGitBlog

A static blog engine powered entirely by **GitHub Issues** — no CMS, no database, no complex infrastructure.

Write posts in GitHub's rich Markdown editor. Comments come from Issue comments. Everything is compiled into a clean static HTML site and deployed to GitHub Pages automatically.

---

## ✨ Features

- **WYSIWYG authoring** — use GitHub's built-in Issue editor (Markdown, images, code blocks, mentions)
- **Comment threads** — Issue comments become blog comment threads
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

### Blocking users

Add GitHub usernames (one per line) to `config/blocked_users.txt` to prevent their comments from appearing:

```
# config/blocked_users.txt
spambot123
another_bad_actor
```

Lines starting with `#` are treated as comments and ignored.

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
GitHub Issues (label: blog-post)
        │
        ▼
blog/generate.py   ← fetches via GitHub REST API
        │
        ├── Converts Markdown → HTML  (python-markdown)
        ├── Sanitises HTML            (bleach allowlist)
        ├── Renders Jinja2 templates
        └── Writes _site/
                 │
                 ▼
        GitHub Actions (build-blog.yml)
                 │
                 ▼
        GitHub Pages  →  your live blog
```

### Key files

| Path | Purpose |
|------|---------|
| `blog/generate.py` | Main site generator |
| `blog/templates/` | Jinja2 HTML templates |
| `blog/static/` | CSS and favicon |
| `config/blocked_users.txt` | Blocked commenter usernames |
| `.github/workflows/build-blog.yml` | CI/CD pipeline |
| `requirements.txt` | Python dependencies |

---

## 🛡️ Security

- All content rendered from Issues and comments passes through `bleach.clean()` with a strict tag/attribute allowlist.
- `javascript:` and `data:` URI schemes are stripped from all `href` and `src` attributes.
- All links in user content receive `rel="nofollow noopener noreferrer"`.

---

## Credits

Built with:
- [python-markdown](https://python-markdown.github.io/)
- [bleach](https://bleach.readthedocs.io/)
- [Jinja2](https://jinja.palletsprojects.com/)
- [requests](https://requests.readthedocs.io/)
- [peaceiris/actions-gh-pages](https://github.com/peaceiris/actions-gh-pages)
