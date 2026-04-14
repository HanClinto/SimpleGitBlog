"""
Stage 02 — Fetch GitHub Issues (My Writing).

Writes: _cache/issues.json

Environment variables consumed:
  GITHUB_REPOSITORY   required  owner/repo
  GITHUB_TOKEN        optional  PAT for higher rate limits and collaborator lookup
"""

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from blog.ingestors import github_issues  # noqa: E402
from blog.pipeline_cache import write_cache, emit_gha_warnings  # noqa: E402

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("ERROR: GITHUB_REPOSITORY is not set.", file=sys.stderr)
        sys.exit(1)

    token = os.environ.get("GITHUB_TOKEN") or None

    print("Fetching GitHub Issues (My Writing)…")
    start = time.monotonic()
    posts, warnings = github_issues.ingest(repo, token, _CONFIG_DIR)
    elapsed = time.monotonic() - start

    print(f"  {len(posts)} post(s) ingested from GitHub Issues.")
    emit_gha_warnings(warnings)

    write_cache("issues", {
        "stage": "issues",
        "posts": posts,
        "warnings": warnings,
        "elapsed": elapsed,
        "skipped": False,
    })
    print(f"  Stage complete in {elapsed:.1f}s ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    main()
