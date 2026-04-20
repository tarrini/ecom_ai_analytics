import json
import os
from datetime import datetime

from dotenv import load_dotenv
from google.cloud import aiplatform

load_dotenv()


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


def main() -> None:
    project_id = _env("GCP_PROJECT_ID", required=True)
    region = _env("GCP_REGION", required=True)
    pipeline_root = _env("VERTEX_PIPELINE_ROOT", required=True)
    service_account = _env("VERTEX_SERVICE_ACCOUNT", required=True)
    template_path = _env("VERTEX_PIPELINE_TEMPLATE_PATH", required=True)
    display_name_prefix = _env("VERTEX_PIPELINE_DISPLAY_NAME_PREFIX", default="ecom-analytics")
    pipeline_params = _load_parameters(_env("VERTEX_PIPELINE_PARAMS_JSON", default="{}"))

    run_suffix = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
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
    job.submit(service_account=service_account)
    print(f"Vertex pipeline submitted successfully: {job.resource_name}")


if __name__ == "__main__":
    main()
