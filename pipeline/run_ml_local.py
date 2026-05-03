
from __future__ import annotations
import os
import sys
from pathlib import Path
_PIPELINE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PIPELINE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env", override=True)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _as_bool(name: str, default: str = "true") -> bool:
    return _env(name, default).lower() in {"1", "true", "yes", "y"}


def main() -> None:
    if not _as_bool("RUN_ML_TRAINING", "true"):
        print("RUN_ML_TRAINING is false — skipping ML training block.")
        return

    required = [
        "GCP_PROJECT_ID",
        "GCP_REGION",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
    ]
    missing = [k for k in required if not _env(k)]
    if missing:
        raise SystemExit(f"Missing env for ML: {', '.join(missing)}")

    from pipeline.components.train_forecast_op import run_train_forecast_op
    from pipeline.components.train_delay_op import run_train_delay_op
    from pipeline.components.drift_registry_op import run_drift_and_registry_op

    common = dict(
        snowflake_account=_env("SNOWFLAKE_ACCOUNT"),
        snowflake_user=_env("SNOWFLAKE_USER"),
        snowflake_password=_env("SNOWFLAKE_PASSWORD"),
        snowflake_warehouse=_env("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        snowflake_database=_env("SNOWFLAKE_DATABASE", "ECOM_ANALYTICS"),
        snowflake_schema=_env("SNOWFLAKE_SCHEMA", "RAW"),
        snowflake_role=_env("SNOWFLAKE_ROLE", ""),
    )
    pid, region = _env("GCP_PROJECT_ID"), _env("GCP_REGION")

    print("1/3 Training forecast model…")
    fc_id, fc_metric = run_train_forecast_op(project_id=pid, region=region, **common)
    print(f"   forecast model: {fc_id}  MAPE: {fc_metric}")

    print("2/3 Training delay-risk model…")
    d_id, d_metric = run_train_delay_op(project_id=pid, region=region, **common)
    print(f"   delay model: {d_id}  ROC-AUC: {d_metric}")

    print("3/3 Drift + model registry…")
    ch_fc, ch_d = run_drift_and_registry_op(
        project_id=pid,
        region=region,
        **common,
        forecast_model_resource=fc_id,
        forecast_metric_value=fc_metric,
        delay_model_resource=d_id,
        delay_metric_value=d_metric,
    )
    print(f"   champions — forecast: {ch_fc}\n   champions — delay: {ch_d}")
    print("ML block finished successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ML block failed: {exc}", file=sys.stderr)
        sys.exit(1)
