"""
Stage 06 — Fetch GitHub merged PRs into other repositories.

Writes: _cache/contributions.json

Environment variables consumed:
  GITHUB_REPOSITORY            required  owner/repo; owner is the default user
  GITHUB_TOKEN                 optional  PAT for higher rate limits
  GITHUB_CONTRIBUTIONS_USER    optional  GitHub login to search for
  GITHUB_CONTRIBUTIONS_LIMIT   optional  number of PRs to keep (default: 8)
"""

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from blog.ingestors import github_contributions  # noqa: E402
from blog.pipeline_cache import write_cache, emit_gha_warnings  # noqa: E402


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("ERROR: GITHUB_REPOSITORY is not set.", file=sys.stderr)
        sys.exit(1)

    user = os.environ.get("GITHUB_CONTRIBUTIONS_USER", "").strip() or repo.split("/")[0]
    token = os.environ.get("GITHUB_TOKEN") or None
    limit = int(os.environ.get("GITHUB_CONTRIBUTIONS_LIMIT", "8"))

    print(f"Fetching GitHub contributions for: {user}…")
    start = time.monotonic()
    posts, warnings = github_contributions.ingest(user, token, limit=limit)
    elapsed = time.monotonic() - start

    print(f"  {len(posts)} merged PR contribution(s) ingested.")
    emit_gha_warnings(warnings)

    write_cache("contributions", {
        "stage": "contributions",
        "posts": posts,
        "user": user,
        "warnings": warnings,
        "elapsed": elapsed,
        "skipped": False,
    })
    print(f"  Stage complete in {elapsed:.1f}s ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    main()
