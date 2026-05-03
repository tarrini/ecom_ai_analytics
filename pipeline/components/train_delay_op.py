
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
from scipy.sparse import csr_matrix, hstack
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

from pipeline.vertex_staging_bucket import resolve_vertex_staging_bucket_uri_from_env


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

    df.columns = pd.Index([str(c).lower() for c in df.columns])

    target = "is_delayed"
    meta_exclude = {target, "order_id", "order_purchase_ts"}
    cat_cols = [c for c in df.columns if df[c].dtype == "object" and c not in meta_exclude]
    num_cols = [c for c in df.columns if c not in cat_cols and c not in meta_exclude]
    if not num_cols and not cat_cols:
        raise RuntimeError("No feature columns left after excluding target/metadata")

    def build_numeric(df_part: pd.DataFrame) -> pd.DataFrame:
        if not num_cols:
            return pd.DataFrame(index=df_part.index)
        Xn = df_part[list(num_cols)].copy()
        for col in num_cols:
            Xn[col] = pd.to_numeric(Xn[col], errors="coerce")
        return Xn.fillna(0.0).astype(np.float32)

    y_arr = df[target].astype(np.int32).to_numpy()
    idx_all = np.arange(len(df))

    def build_fold_sparse(train_idx: np.ndarray, val_idx: np.ndarray):
        df_tr = df.iloc[train_idx]
        df_va = df.iloc[val_idx]
        Xn_tr = build_numeric(df_tr)
        Xn_va = build_numeric(df_va)
        if cat_cols:
            enc = OneHotEncoder(handle_unknown="ignore", sparse_output=True)
            C_tr = enc.fit_transform(df_tr[cat_cols].astype(str).fillna("__nan__"))
            C_va = enc.transform(df_va[cat_cols].astype(str).fillna("__nan__"))
            if num_cols:
                X_tr = hstack([csr_matrix(Xn_tr.values), C_tr], format="csr")
                X_va = hstack([csr_matrix(Xn_va.values), C_va], format="csr")
            else:
                X_tr = C_tr.tocsr()
                X_va = C_va.tocsr()
        else:
            X_tr = csr_matrix(Xn_tr.values)
            X_va = csr_matrix(Xn_va.values)
        return X_tr, X_va, y_arr[train_idx], y_arr[val_idx]

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    auc_scores: list[float] = []

    for tr_idx, va_idx in skf.split(idx_all, y_arr):
        X_tr, X_va, y_tr, y_va = build_fold_sparse(tr_idx, va_idx)
        if np.unique(y_tr).size < 2 or np.unique(y_va).size < 2:
            continue
        fold_model = XGBClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.9,
            eval_metric="logloss",
            random_state=42,
        )
        fold_model.fit(X_tr, y_tr)
        prob = fold_model.predict_proba(X_va)[:, 1]
        auc_scores.append(roc_auc_score(y_va, prob))

    if not auc_scores:
        try:
            tr_idx, va_idx = train_test_split(
                idx_all,
                test_size=0.25,
                stratify=y_arr,
                random_state=42,
            )
        except ValueError:
            tr_idx, va_idx = train_test_split(idx_all, test_size=0.25, random_state=42)
        X_tr, X_va, y_tr, y_va = build_fold_sparse(tr_idx, va_idx)
        if np.unique(y_va).size < 2:
            raise RuntimeError(
                "[delay] Cannot compute ROC-AUC: validation fold has only one class — check training data balance."
            )
        fold_model = XGBClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.9,
            eval_metric="logloss",
            random_state=42,
        )
        fold_model.fit(X_tr, y_tr)
        auc_scores.append(roc_auc_score(y_va, fold_model.predict_proba(X_va)[:, 1]))

    metric_value = float(np.mean(auc_scores))

    Xn_full = build_numeric(df)
    if cat_cols:
        enc_final = OneHotEncoder(handle_unknown="ignore", sparse_output=True)
        C_full = enc_final.fit_transform(df[cat_cols].astype(str).fillna("__nan__"))
        X_full = (
            hstack([csr_matrix(Xn_full.values), C_full], format="csr")
            if num_cols
            else C_full.tocsr()
        )
        best_model = XGBClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.9,
            eval_metric="logloss",
            random_state=42,
        )
        best_model.fit(X_full, y_arr)
        artifact = {
            "model": best_model,
            "num_cols": num_cols,
            "cat_cols": cat_cols,
            "onehot": enc_final,
        }
    else:
        X_full = csr_matrix(Xn_full.values)
        best_model = XGBClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.9,
            eval_metric="logloss",
            random_state=42,
        )
        best_model.fit(X_full, y_arr)
        artifact = {"model": best_model, "num_cols": num_cols, "cat_cols": [], "onehot": None}

    staging_uri = resolve_vertex_staging_bucket_uri_from_env()
    aiplatform.init(project=project_id, location=region, staging_bucket=staging_uri)

    with tempfile.TemporaryDirectory() as td:
        joblib.dump(artifact, os.path.join(td, "model.joblib"))

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
