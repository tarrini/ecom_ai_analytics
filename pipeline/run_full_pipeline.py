import os
import subprocess
import sys
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _build_steps() -> List[List[str]]:
    steps: List[List[str]] = [
        ["python", "pipeline/ingest_kaggle_to_gcs.py"],
        ["python", "pipeline/load_gcs_to_snowflake.py"],
        ["python", "pipeline/trigger_snowflake_tasks.py"],
    ]

    if _as_bool(os.getenv("RUN_ML_TRAINING", "true")):
        steps.append(["python", "pipeline/run_ml_local.py"])

    steps.append(["python", "pipeline/run_tableau_refresh.py"])

    if _as_bool(os.getenv("RUN_LLM_BRIEF", "true")):
        steps.append(["python", "pipeline/generate_daily_client_brief.py"])

    if _as_bool(os.getenv("RUN_VERTEX_PIPELINE", "false")):
        steps.append(["python", "pipeline/run_vertex_ai_pipeline_job.py"])

    return steps


def main() -> None:
    for step in _build_steps():
        print(f"\nRunning: {' '.join(step)}")
        result = subprocess.run(step)
        if result.returncode != 0:
            print(f"Step failed: {' '.join(step)}")
            sys.exit(result.returncode)

    print("\nFull end-to-end pipeline finished successfully.")


if __name__ == "__main__":
    main()
