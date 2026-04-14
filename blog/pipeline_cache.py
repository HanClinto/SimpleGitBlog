"""
Shared helpers for the numbered pipeline scripts (01_fetch_profile.py … 05_render.py).

Each fetch script writes its result to ``_cache/{stage}.json``.
Stage 05 (render) reads all of them to build the site.

The cache directory is ephemeral (gitignored) and lives only for the
duration of a single pipeline run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CACHE_DIR = Path(os.environ.get("PIPELINE_CACHE_DIR", "_cache"))


def cache_path(stage: str) -> Path:
    return CACHE_DIR / f"{stage}.json"


def write_cache(stage: str, payload: dict) -> None:
    """Write *payload* to ``_cache/{stage}.json``, creating the directory if needed."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(stage).write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )


def read_cache(stage: str) -> dict:
    """Read ``_cache/{stage}.json``.

    Returns a safe empty-result dict when the file is missing — for example
    when the fetch step crashed or was skipped via ``continue-on-error``.
    """
    path = cache_path(stage)
    if not path.exists():
        return {
            "posts": [],
            "warnings": [
                f"No cache file found for stage '{stage}' — the fetch step may have"
                " failed or been skipped."
            ],
            "elapsed": 0.0,
            "skipped": True,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def emit_gha_warnings(warnings: list[str]) -> None:
    """Print GitHub Actions ``::warning::`` annotations for each collected warning.

    These appear as yellow annotation badges on the workflow run summary page
    and in the step log.  Has no effect outside GitHub Actions.
    """
    for w in warnings:
        # Strip the leading "Warning: " prefix that ingestors add for console
        # readability — the GHA annotation already implies it is a warning.
        clean = w.strip()
        if clean.lower().startswith("warning:"):
            clean = clean[len("warning:"):].strip()
        print(f"::warning::{clean}", flush=True)
