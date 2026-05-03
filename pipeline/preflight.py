import os
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _required_env(run_ml: bool, run_tableau: bool, run_llm: bool, run_vertex: bool) -> Dict[str, List[str]]:
    base = [
        "KAGGLE_USERNAME",
        "KAGGLE_KEY",
        "KAGGLE_DATASET",
        "GCS_BUCKET",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    ]
    groups: Dict[str, List[str]] = {"base": base}

    if run_ml:
        groups["ml"] = ["GCP_PROJECT_ID", "GCP_REGION"]
    if run_tableau:
        groups["tableau"] = ["TABLEAU_SERVER_URL", "TABLEAU_PAT_NAME", "TABLEAU_PAT_SECRET"]
    if run_llm:
        groups["llm"] = ["OPENAI_API_KEY"]
    if run_vertex:
        groups["vertex"] = [
            "GCP_PROJECT_ID",
            "GCP_REGION",
            "VERTEX_PIPELINE_ROOT",
            "VERTEX_PIPELINE_TEMPLATE_PATH",
            "VERTEX_SERVICE_ACCOUNT",
        ]
    return groups


def validate_env() -> None:
    run_ml = _as_bool(os.getenv("RUN_ML_TRAINING", "true"), default=True)
    run_tableau = _as_bool(os.getenv("RUN_TABLEAU_REFRESH", "true"), default=True)
    run_llm = _as_bool(os.getenv("RUN_LLM_BRIEF", "true"), default=True)
    run_vertex = _as_bool(os.getenv("RUN_VERTEX_PIPELINE", "false"), default=False)

    groups = _required_env(run_ml, run_tableau, run_llm, run_vertex)
    missing_by_group: Dict[str, List[str]] = {}

    for group_name, keys in groups.items():
        missing = [k for k in keys if not os.getenv(k, "").strip()]
        if missing:
            missing_by_group[group_name] = missing

    if missing_by_group:
        lines = ["Missing required environment variables:"]
        for group_name, keys in missing_by_group.items():
            lines.append(f"- {group_name}: {', '.join(keys)}")
        raise SystemExit("\n".join(lines))

    if run_vertex:
        staging_ok = bool(os.getenv("VERTEX_AI_GCS_STAGING_BUCKET", "").strip())
        if not staging_ok:
            root = os.getenv("VERTEX_PIPELINE_ROOT", "").strip()
            staging_ok = root.startswith("gs://") and bool(root[5:].split("/")[0])
        if not staging_ok:
            raise SystemExit(
                "RUN_VERTEX_PIPELINE=true requires VERTEX_AI_GCS_STAGING_BUCKET=gs://YOUR_BUCKET "
                "or VERTEX_PIPELINE_ROOT=gs://YOUR_BUCKET/... so ML steps can stage models to GCS."
            )


if __name__ == "__main__":
    validate_env()
    print("Preflight check passed.")
