
from __future__ import annotations

import os
import tempfile
from typing import Tuple

import joblib
import numpy as np
import pandas as pd
import snowflake.connector
from google.cloud import aiplatform
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier


def run_train_delay_op(
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
    cur.execute("SELECT * FROM ECOM_ANALYTICS.FEATURES.FCT_DELAY_FEATURES_TRAIN")
    df = cur.fetch_pandas_all()
    cur.close()
    conn.close()

    if df.empty:
        raise RuntimeError("No training rows in FEATURES.FCT_DELAY_FEATURES_TRAIN")

    target = "is_delayed"
    cat_cols = [c for c in df.columns if df[c].dtype == "object" and c != target]
    df = pd.get_dummies(df, columns=cat_cols, dummy_na=True)
    feature_cols = [c for c in df.columns if c not in [target, "order_id", "order_purchase_ts"]]

    X = df[feature_cols].fillna(0)
    y = df[target].astype(int)

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    auc_scores: list[float] = []
    best_model = None

    for tr, va in skf.split(X, y):
        xtr, xva = X.iloc[tr], X.iloc[va]
        ytr, yva = y.iloc[tr], y.iloc[va]
        model = XGBClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.9,
            eval_metric="logloss",
            random_state=42,
        )
        model.fit(xtr, ytr)
        prob = model.predict_proba(xva)[:, 1]
        auc = roc_auc_score(yva, prob)
        auc_scores.append(auc)
        best_model = model

    metric_value = float(np.mean(auc_scores))

    aiplatform.init(project=project_id, location=region)

    with tempfile.TemporaryDirectory() as td:
        model_path = os.path.join(td, "delay_model.joblib")
        joblib.dump({"model": best_model, "features": feature_cols}, model_path)

        uploaded = aiplatform.Model.upload(
            display_name="ecom-delay-risk-model",
            artifact_uri=td,
            serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-5:latest",
            labels={"task": "delay_risk", "framework": "xgboost"},
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

    rid, m = run_train_delay_op(
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
    print("delay model:", rid, "ROC-AUC:", m)
