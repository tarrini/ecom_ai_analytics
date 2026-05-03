import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
from google.cloud import aiplatform

from pipeline.vertex_staging_bucket import (
    normalize_vertex_staging_bucket_uri,
    resolve_vertex_staging_bucket_uri_from_env,
)

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _env(name: str, required: bool = False, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if required and not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def _load_parameters(raw_params: str) -> dict:
    if not raw_params:
        return {}
    try:
        parsed = json.loads(raw_params)
    except json.JSONDecodeError as exc:
        raise ValueError("VERTEX_PIPELINE_PARAMS_JSON must be a valid JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError("VERTEX_PIPELINE_PARAMS_JSON must decode to a JSON object")
    return parsed


def _merge_pipeline_params_from_env(parameter_values: dict, template_path: str) -> dict:
    """Fill missing pipeline root inputs from env (VERTEX_PIPELINE_PARAMS_JSON often {})."""
    out = dict(parameter_values)
    llm_only = "llm_only" in template_path.lower()

    def fill(key: str, env_key: str) -> None:
        cur = out.get(key)
        if cur is not None and str(cur).strip() != "":
            return
        env_val = os.getenv(env_key, "").strip()
        if env_val:
            out[key] = env_val

    if not llm_only:
        fill("project_id", "GCP_PROJECT_ID")
        fill("region", "GCP_REGION")
        fill("vertex_gcs_staging_bucket", "VERTEX_AI_GCS_STAGING_BUCKET")

    fill("snowflake_account", "SNOWFLAKE_ACCOUNT")
    fill("snowflake_user", "SNOWFLAKE_USER")
    fill("snowflake_password", "SNOWFLAKE_PASSWORD")
    fill("snowflake_warehouse", "SNOWFLAKE_WAREHOUSE")
    fill("snowflake_database", "SNOWFLAKE_DATABASE")
    fill("snowflake_schema", "SNOWFLAKE_SCHEMA")
    fill("snowflake_role", "SNOWFLAKE_ROLE")
    fill("openai_api_key", "OPENAI_API_KEY")
    fill("openai_model", "OPENAI_MODEL")

    if not llm_only:
        missing = [k for k in ("project_id", "region") if not str(out.get(k, "")).strip()]
        if missing:
            raise ValueError(
                "Missing pipeline parameters "
                + ", ".join(missing)
                + ": set them in VERTEX_PIPELINE_PARAMS_JSON or export GCP_PROJECT_ID / GCP_REGION"
            )

        vb = str(out.get("vertex_gcs_staging_bucket", "")).strip()
        if vb:
            out["vertex_gcs_staging_bucket"] = normalize_vertex_staging_bucket_uri(vb)
        else:
            try:
                out["vertex_gcs_staging_bucket"] = resolve_vertex_staging_bucket_uri_from_env()
            except ValueError:
                pass
        if not str(out.get("vertex_gcs_staging_bucket", "")).strip():
            raise ValueError(
                "Missing vertex_gcs_staging_bucket (Model.upload requires a GCS staging bucket): "
                "set VERTEX_AI_GCS_STAGING_BUCKET=gs://YOUR_BUCKET or VERTEX_PIPELINE_ROOT=gs://YOUR_BUCKET/..."
            )

    if not str(out.get("openai_api_key", "")).strip():
        raise ValueError(
            "Missing openai_api_key for Vertex pipeline: set OPENAI_API_KEY or VERTEX_PIPELINE_PARAMS_JSON"
        )

    sf_required = (
        "snowflake_account",
        "snowflake_user",
        "snowflake_password",
        "snowflake_warehouse",
        "snowflake_database",
        "snowflake_schema",
    )
    missing_sf = [k for k in sf_required if not str(out.get(k, "")).strip()]
    if missing_sf:
        raise ValueError(
            "Missing Snowflake pipeline parameters "
            + ", ".join(missing_sf)
            + ": set Snowflake env vars or VERTEX_PIPELINE_PARAMS_JSON"
        )

    return out


def main() -> None:
    project_id = _env("GCP_PROJECT_ID", required=True)
    region = _env("GCP_REGION", required=True)
    pipeline_root = _env("VERTEX_PIPELINE_ROOT", required=True)
    service_account = _env("VERTEX_SERVICE_ACCOUNT", required=True)
    template_path = _env("VERTEX_PIPELINE_TEMPLATE_PATH", required=True)
    display_name_prefix = _env("VERTEX_PIPELINE_DISPLAY_NAME_PREFIX", default="ecom-analytics")
    pipeline_params = _merge_pipeline_params_from_env(
        _load_parameters(_env("VERTEX_PIPELINE_PARAMS_JSON", default="{}")),
        template_path,
    )

    run_suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    display_name = f"{display_name_prefix}-{run_suffix}"

    print(f"Initializing Vertex AI in {project_id}/{region}")
    aiplatform.init(project=project_id, location=region)

    print(f"Submitting Vertex Pipeline Job: {display_name}")
    job = aiplatform.PipelineJob(
        display_name=display_name,
        template_path=template_path,
        pipeline_root=pipeline_root,
        parameter_values=pipeline_params,
        enable_caching=False,
    )
    # Vertex defaults to the Compute Engine default SA (*-compute@developer.gserviceaccount.com)
    # when service_account is omitted; callers rarely have iam.serviceAccountUser on it — set explicitly.
    print(f"Pipeline workload service account: {service_account}")
    job.submit(service_account=service_account)

    print(f"Vertex pipeline job: {job.resource_name}")

    wait = _as_bool(os.getenv("VERTEX_PIPELINE_WAIT_FOR_COMPLETION"), default=True)
    if wait:
        print("Waiting until Vertex pipeline finishes (required before downstream steps e.g. Tableau refresh)…")
        job.wait()
        print(f"Vertex pipeline completed: {job.state}")
    else:
        print(
            "VERTEX_PIPELINE_WAIT_FOR_COMPLETION=false — not waiting; "
            "job may still be RUNNING on Vertex AI."
        )


if __name__ == "__main__":
    main()
