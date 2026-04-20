"""
Compile the full Vertex AI Pipeline (KFP): forecast → delay → drift/registry → LLM brief.

Why KFP was partially avoided earlier:
  KFP 2.x does not accept arbitrary Python return types (e.g. `tuple[str, float]`) from
  @dsl.component — the compiler treats some annotations as artifacts and fails. The fix
  is to pass values between steps with dsl.OutputPath / dsl.InputPath (small text files).

Do not add `from __future__ import annotations` in this file — pipeline parameters must
stay real `str` types for KFP introspection.

From repo root:
  pip install kfp
  python pipeline/vertex_pipeline.py

Outputs:
  pipeline/ecom_vertex_pipeline.json   — full ML + brief (default)
  pipeline/ecom_vertex_pipeline_llm_only.json — brief only (optional second compile)
"""

import sys
from pathlib import Path

_PIPELINE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PIPELINE_DIR.parent
for p in (_REPO_ROOT, _PIPELINE_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from kfp import compiler, dsl

from pipeline.components.kfp_ml_components import (
    drift_registry_vertex_component,
    train_delay_vertex_component,
    train_forecast_vertex_component,
)
from pipeline.components.llm_brief_op import llm_daily_brief_op


@dsl.pipeline(
    name="ecom-vertex-full",
    display_name="Ecom — train forecast, delay, drift, daily brief",
)
def ecom_full_vertex_pipeline(
    project_id: str,
    region: str,
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
    openai_api_key: str,
    openai_model: str = "gpt-4o-mini",
):
    fc = train_forecast_vertex_component(
        project_id=project_id,
        region=region,
        snowflake_account=snowflake_account,
        snowflake_user=snowflake_user,
        snowflake_password=snowflake_password,
        snowflake_warehouse=snowflake_warehouse,
        snowflake_database=snowflake_database,
        snowflake_schema=snowflake_schema,
        snowflake_role=snowflake_role,
    )
    dl = train_delay_vertex_component(
        project_id=project_id,
        region=region,
        snowflake_account=snowflake_account,
        snowflake_user=snowflake_user,
        snowflake_password=snowflake_password,
        snowflake_warehouse=snowflake_warehouse,
        snowflake_database=snowflake_database,
        snowflake_schema=snowflake_schema,
        snowflake_role=snowflake_role,
    )
    drift_task = drift_registry_vertex_component(
        project_id=project_id,
        region=region,
        snowflake_account=snowflake_account,
        snowflake_user=snowflake_user,
        snowflake_password=snowflake_password,
        snowflake_warehouse=snowflake_warehouse,
        snowflake_database=snowflake_database,
        snowflake_schema=snowflake_schema,
        snowflake_role=snowflake_role,
        forecast_model_in=fc.outputs["forecast_model_resource_path"],
        forecast_metric_in=fc.outputs["forecast_metric_path"],
        delay_model_in=dl.outputs["delay_model_resource_path"],
        delay_metric_in=dl.outputs["delay_metric_path"],
    ).after(fc, dl)

    llm_daily_brief_op(
        snowflake_account=snowflake_account,
        snowflake_user=snowflake_user,
        snowflake_password=snowflake_password,
        snowflake_warehouse=snowflake_warehouse,
        snowflake_database=snowflake_database,
        snowflake_schema=snowflake_schema,
        snowflake_role=snowflake_role,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
    ).after(drift_task)


@dsl.pipeline(
    name="ecom-llm-brief-only",
    display_name="Ecom — daily KPI brief (OpenAI) only",
)
def ecom_llm_only_vertex_pipeline(
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
    openai_api_key: str,
    openai_model: str = "gpt-4o-mini",
):
    llm_daily_brief_op(
        snowflake_account=snowflake_account,
        snowflake_user=snowflake_user,
        snowflake_password=snowflake_password,
        snowflake_warehouse=snowflake_warehouse,
        snowflake_database=snowflake_database,
        snowflake_schema=snowflake_schema,
        snowflake_role=snowflake_role,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
    )


def main() -> None:
    full_path = _PIPELINE_DIR / "ecom_vertex_pipeline.json"
    llm_path = _PIPELINE_DIR / "ecom_vertex_pipeline_llm_only.json"

    compiler.Compiler().compile(
        pipeline_func=ecom_full_vertex_pipeline,
        package_path=str(full_path),
    )
    print(f"Compiled full pipeline: {full_path}")

    compiler.Compiler().compile(
        pipeline_func=ecom_llm_only_vertex_pipeline,
        package_path=str(llm_path),
    )
    print(f"Compiled LLM-only pipeline: {llm_path}")


if __name__ == "__main__":
    main()
