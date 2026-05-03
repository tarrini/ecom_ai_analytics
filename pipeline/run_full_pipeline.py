import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv

_PIPELINE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PIPELINE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.preflight import validate_env

load_dotenv(override=True)


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _build_steps() -> List[Tuple[str, List[str]]]:
    steps: List[Tuple[str, List[str]]] = [
        ("ingest_kaggle_to_gcs", ["python", "pipeline/ingest_kaggle_to_gcs.py"]),
        ("load_gcs_to_snowflake", ["python", "pipeline/load_gcs_to_snowflake.py"]),
        ("trigger_snowflake_tasks", ["python", "pipeline/trigger_snowflake_tasks.py"]),
    ]

    if _as_bool(os.getenv("RUN_ML_TRAINING", "true")):
        steps.append(("run_ml_local", ["python", "pipeline/run_ml_local.py"]))

    if _as_bool(os.getenv("RUN_LLM_BRIEF", "true")):
        steps.append(("generate_daily_client_brief", ["python", "pipeline/generate_daily_client_brief.py"]))

    if _as_bool(os.getenv("RUN_VERTEX_PIPELINE", "false")):
        steps.append(("run_vertex_ai_pipeline_job", ["python", "pipeline/run_vertex_ai_pipeline_job.py"]))

    if _as_bool(os.getenv("RUN_TABLEAU_REFRESH", "true")):
        steps.append(("run_tableau_refresh", ["python", "pipeline/run_tableau_refresh.py"]))

    return steps


def main() -> None:
    validate_env()
    for name, step in _build_steps():
        print(f"\nRunning [{name}]: {' '.join(step)}")
        result = subprocess.run(step)
        if result.returncode != 0:
            print(f"Step failed [{name}]: {' '.join(step)}")
            sys.exit(result.returncode)

    print("\nFull end-to-end pipeline finished successfully.")


if __name__ == "__main__":
    main()
