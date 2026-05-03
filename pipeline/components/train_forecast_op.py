
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import joblib
import numpy as np
import pandas as pd
import snowflake.connector
from google.cloud import aiplatform
from sklearn.metrics import mean_absolute_percentage_error
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from xgboost import XGBRegressor

from pipeline.vertex_staging_bucket import resolve_vertex_staging_bucket_uri_from_env


def run_train_forecast_op(
    project_id: str,
    region: str,
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
) -> Tuple[str, float]:
    conn = snowflake.connector.connect(
        account=snowflake_account,
        user=snowflake_user,
        password=snowflake_password,
        warehouse=snowflake_warehouse,
        database=snowflake_database,
        schema=snowflake_schema,
        role=snowflake_role,
    )
    cur = conn.cursor()
    cur.execute("SELECT * FROM ECOM_ANALYTICS.FEATURES.FCT_DEMAND_FEATURES_TRAIN")
    colnames = [str(d[0]).lower() for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    df = pd.DataFrame.from_records(rows, columns=colnames)

    if df.empty:
        raise RuntimeError("No training rows in FEATURES.FCT_DEMAND_FEATURES_TRAIN")

    target = "target_orders_next_7d"
    feature_cols = [
        c
        for c in df.columns
        if c not in ["kpi_date", "customer_state", "product_category_name", target]
    ]
    df = df.sort_values("kpi_date").dropna(subset=[target])

    X = df[feature_cols].copy()
    for _col in X.columns:
        X[_col] = pd.to_numeric(X[_col], errors="coerce")
    X = X.fillna(0.0).astype(np.float64)
    y = pd.to_numeric(df[target], errors="coerce").fillna(0.0).astype(np.float64)

    tscv = TimeSeriesSplit(n_splits=4)
    scores: list[float] = []
    best_model = None
    for train_idx, val_idx in tscv.split(X):
        xtr, xva = X.iloc[train_idx], X.iloc[val_idx]
        ytr, yva = y.iloc[train_idx], y.iloc[val_idx]

        model = XGBRegressor(
            n_estimators=400,
            learning_rate=0.05,
            max_depth=7,
            subsample=0.9,
            colsample_bytree=0.9,
            tree_method="hist",
            random_state=42,
        )
        model.fit(xtr, ytr)
        pred = model.predict(xva)
        yva_np = np.maximum(yva.to_numpy(dtype=float, copy=False), 1e-8)
        pred_np = np.maximum(pred, 1e-8)
        mape = mean_absolute_percentage_error(yva_np, pred_np)
        scores.append(mape)
        best_model = model

    metric_value = float(np.mean(scores))
    if best_model is None:
        raise RuntimeError("[forecast] No trained model produced")

    staging_uri = resolve_vertex_staging_bucket_uri_from_env()
    aiplatform.init(project=project_id, location=region, staging_bucket=staging_uri)

    with tempfile.TemporaryDirectory() as td:
        serving_pipeline = Pipeline([("regressor", best_model)])
        joblib.dump(serving_pipeline, os.path.join(td, "model.joblib"))

        uploaded = aiplatform.Model.upload(
            display_name="ecom-forecast-model",
            artifact_uri=td,
            serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-5:latest",
            labels={"task": "forecast", "framework": "xgboost"},
        )

    return uploaded.resource_name, metric_value


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    required = ["GCP_PROJECT_ID", "GCP_REGION", "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]
    missing = [k for k in required if not _env(k)]
    if missing:
        raise SystemExit(f"Missing env: {', '.join(missing)}")

    rid, m = run_train_forecast_op(
        project_id=_env("GCP_PROJECT_ID"),
        region=_env("GCP_REGION"),
        snowflake_account=_env("SNOWFLAKE_ACCOUNT"),
        snowflake_user=_env("SNOWFLAKE_USER"),
        snowflake_password=_env("SNOWFLAKE_PASSWORD"),
        snowflake_warehouse=_env("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        snowflake_database=_env("SNOWFLAKE_DATABASE", "ECOM_ANALYTICS"),
        snowflake_schema=_env("SNOWFLAKE_SCHEMA", "RAW"),
        snowflake_role=_env("SNOWFLAKE_ROLE", ""),
    )
    print("forecast model:", rid, "MAPE:", m)
