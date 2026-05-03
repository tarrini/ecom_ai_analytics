"""Resolve gs://bucket for aiplatform.init(..., staging_bucket=...) when uploading local model dirs."""

from __future__ import annotations

import os


def normalize_vertex_staging_bucket_uri(raw: str) -> str:
    """Return gs://BUCKET from gs://bucket/prefix, gs://bucket, or bare bucket name."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("vertex staging bucket URI is empty")
    if not s.startswith("gs://"):
        s = f"gs://{s}"
    bucket = s[5:].split("/")[0]
    if not bucket:
        raise ValueError(f"could not parse GCS bucket from {raw!r}")
    return f"gs://{bucket}"


def resolve_vertex_staging_bucket_uri_from_env() -> str:
    explicit = os.getenv("VERTEX_AI_GCS_STAGING_BUCKET", "").strip()
    if explicit:
        return normalize_vertex_staging_bucket_uri(explicit)
    root = os.getenv("VERTEX_PIPELINE_ROOT", "").strip()
    if root.startswith("gs://"):
        return normalize_vertex_staging_bucket_uri(root)
    raise ValueError(
        "Set VERTEX_AI_GCS_STAGING_BUCKET=gs://YOUR_BUCKET or VERTEX_PIPELINE_ROOT=gs://YOUR_BUCKET/..."
    )
