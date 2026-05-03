
from kfp import dsl


@dsl.component(
    base_image="python:3.11",
    packages_to_install=[
        "grpcio",
        "google-api-core",
        "pandas==2.2.3",
        "numpy>=1.26.4,<2",
        "snowflake-connector-python==4.4.0",
        "google-cloud-aiplatform>=1.38.0",
        "google-cloud-storage>=2.14.0",
        "scikit-learn==1.5.2",
        "xgboost==2.1.1",
        "joblib==1.4.2",
    ],
)
def train_forecast_vertex_component(
    project_id: str,
    region: str,
    vertex_gcs_staging_bucket: str,
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
    forecast_model_resource_path: dsl.OutputPath(),
    forecast_metric_path: dsl.OutputPath(),
) -> None:
    import os
    import tempfile

    import joblib
    import numpy as np
    import pandas as pd
    import snowflake.connector
    from google.cloud import aiplatform
    from sklearn.metrics import mean_absolute_percentage_error
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.pipeline import Pipeline
    from xgboost import XGBRegressor

    try:
        conn = snowflake.connector.connect(
            account=snowflake_account,
            user=snowflake_user,
            password=snowflake_password,
            warehouse=snowflake_warehouse,
            database=snowflake_database,
            schema=snowflake_schema,
            role=snowflake_role or None,
        )
    except Exception as exc:
        raise RuntimeError(
            f"[forecast] Snowflake connect failed (account={snowflake_account}, user={snowflake_user}): {exc}"
        ) from exc
    cur = conn.cursor()
    cur.execute("SELECT * FROM ECOM_ANALYTICS.FEATURES.FCT_DEMAND_FEATURES_TRAIN")
    colnames = [str(d[0]).lower() for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    # Pure Python rows → DataFrame avoids Snowflake pandas/Arrow hooks (255002).
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

    # Snowflake → DataFrame can leave numerics as object; XGBoost requires float/int/bool.
    X = df[feature_cols].copy()
    for _col in X.columns:
        X[_col] = pd.to_numeric(X[_col], errors="coerce")
    X = X.fillna(0.0).astype(np.float64)
    y = pd.to_numeric(df[target], errors="coerce").fillna(0.0).astype(np.float64)

    best_model = None

    def _fit_eval_fold(scores_list, xtr, xva, ytr, yva):
        nonlocal best_model
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
        yva_arr = (
            yva.to_numpy(dtype=float, copy=False)
            if hasattr(yva, "to_numpy")
            else np.asarray(yva, dtype=float)
        )
        yva_np = np.maximum(yva_arr, 1e-8)
        pred_np = np.maximum(pred, 1e-8)
        scores_list.append(mean_absolute_percentage_error(yva_np, pred_np))
        best_model = model

    n = len(X)
    if n < 15:
        raise RuntimeError("[forecast] Too few rows after cleaning for time-series training")

    max_splits = min(4, max(2, n // 40))
    forecast_scores: list[float] = []
    for n_splits_try in range(max_splits, 1, -1):
        scores_try: list[float] = []
        try:
            for train_idx, val_idx in TimeSeriesSplit(n_splits=n_splits_try).split(X):
                xtr, xva = X.iloc[train_idx], X.iloc[val_idx]
                ytr, yva = y.iloc[train_idx], y.iloc[val_idx]
                if len(yva) == 0:
                    continue
                _fit_eval_fold(scores_try, xtr, xva, ytr, yva)
        except ValueError:
            continue
        if scores_try:
            forecast_scores = scores_try
            break

    if not forecast_scores:
        split_at = max(1, int(n * 0.8))
        xtr, xva = X.iloc[:split_at], X.iloc[split_at:]
        ytr, yva = y.iloc[:split_at], y.iloc[split_at:]
        if len(yva) == 0:
            raise RuntimeError("[forecast] Holdout validation fold is empty")
        forecast_scores = []
        _fit_eval_fold(forecast_scores, xtr, xva, ytr, yva)

    metric_value = float(np.mean(forecast_scores))
    if not np.isfinite(metric_value):
        metric_value = 1.0

    if best_model is None:
        raise RuntimeError("[forecast] No trained model produced (CV/holdout produced no folds)")

    raw_staging = (vertex_gcs_staging_bucket or "").strip()
    if not raw_staging:
        raise RuntimeError(
            "[forecast] vertex_gcs_staging_bucket is empty — "
            "set VERTEX_AI_GCS_STAGING_BUCKET or VERTEX_PIPELINE_ROOT=gs://BUCKET/..."
        )
    if not raw_staging.startswith("gs://"):
        raw_staging = f"gs://{raw_staging}"
    _bucket = raw_staging[5:].split("/")[0]
    if not _bucket:
        raise RuntimeError(f"[forecast] invalid vertex_gcs_staging_bucket: {vertex_gcs_staging_bucket!r}")
    staging_uri = f"gs://{_bucket}"
    aiplatform.init(project=project_id, location=region, staging_bucket=staging_uri)

    with tempfile.TemporaryDirectory() as td:
        # sklearn-cpu serving image expects model.joblib or model.pkl at artifact root.
        serving_pipeline = Pipeline([("regressor", best_model)])
        joblib.dump(serving_pipeline, os.path.join(td, "model.joblib"))

        try:
            uploaded = aiplatform.Model.upload(
                display_name="ecom-forecast-model",
                artifact_uri=td,
                serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-5:latest",
                labels={"task": "forecast", "framework": "xgboost"},
            )
        except Exception as exc:
            raise RuntimeError(
                f"[forecast] Model.upload failed (project={project_id}, region={region}): {exc}"
            ) from exc

    rid = uploaded.resource_name
    with open(forecast_model_resource_path, "w", encoding="utf-8") as f:
        f.write(rid)
    with open(forecast_metric_path, "w", encoding="utf-8") as f:
        f.write(str(metric_value))


@dsl.component(
    base_image="python:3.11",
    packages_to_install=[
        "grpcio",
        "google-api-core",
        "pandas==2.2.3",
        "numpy==2.1.3",
        "snowflake-connector-python==4.4.0",
        "google-cloud-aiplatform>=1.38.0",
        "google-cloud-storage>=2.14.0",
        "scikit-learn==1.5.2",
        "xgboost==2.1.1",
        "joblib==1.4.2",
        "scipy>=1.11.0",
    ],
)
def train_delay_vertex_component(
    project_id: str,
    region: str,
    vertex_gcs_staging_bucket: str,
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
    delay_model_resource_path: dsl.OutputPath(),
    delay_metric_path: dsl.OutputPath(),
) -> None:
    import os
    import tempfile

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

    try:
        conn = snowflake.connector.connect(
            account=snowflake_account,
            user=snowflake_user,
            password=snowflake_password,
            warehouse=snowflake_warehouse,
            database=snowflake_database,
            schema=snowflake_schema,
            role=snowflake_role or None,
        )
    except Exception as exc:
        raise RuntimeError(
            f"[delay] Snowflake connect failed (account={snowflake_account}, user={snowflake_user}): {exc}"
        ) from exc
    cur = conn.cursor()
    cur.execute("SELECT * FROM ECOM_ANALYTICS.FEATURES.FCT_DELAY_FEATURES_TRAIN")
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    conn.close()

    df.columns = pd.Index([str(c).lower() for c in df.columns])

    if df.empty:
        raise RuntimeError("No training rows in FEATURES.FCT_DELAY_FEATURES_TRAIN")

    target = "is_delayed"
    meta_exclude = {target, "order_id", "order_purchase_ts"}
    cat_cols = [c for c in df.columns if df[c].dtype == "object" and c not in meta_exclude]
    num_cols = [c for c in df.columns if c not in cat_cols and c not in meta_exclude]
    if not num_cols and not cat_cols:
        raise RuntimeError("[delay] No feature columns left after excluding target/metadata")

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
        return (
            X_tr,
            X_va,
            y_arr[train_idx],
            y_arr[val_idx],
        )

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    auc_scores = []

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
            tr_idx, va_idx = train_test_split(
                idx_all,
                test_size=0.25,
                random_state=42,
            )
        X_tr, X_va, y_tr, y_va = build_fold_sparse(tr_idx, va_idx)
        if np.unique(y_va).size < 2:
            raise RuntimeError(
                "[delay] Cannot compute ROC-AUC: validation fold has only one class in is_delayed — "
                "check FCT_DELAY_FEATURES_TRAIN balance."
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
        prob = fold_model.predict_proba(X_va)[:, 1]
        auc_scores.append(roc_auc_score(y_va, prob))

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

    raw_staging = (vertex_gcs_staging_bucket or "").strip()
    if not raw_staging:
        raise RuntimeError(
            "[delay] vertex_gcs_staging_bucket is empty — "
            "set VERTEX_AI_GCS_STAGING_BUCKET or VERTEX_PIPELINE_ROOT=gs://BUCKET/..."
        )
    if not raw_staging.startswith("gs://"):
        raw_staging = f"gs://{raw_staging}"
    _bucket = raw_staging[5:].split("/")[0]
    if not _bucket:
        raise RuntimeError(f"[delay] invalid vertex_gcs_staging_bucket: {vertex_gcs_staging_bucket!r}")
    staging_uri = f"gs://{_bucket}"
    aiplatform.init(project=project_id, location=region, staging_bucket=staging_uri)

    def _sync_model_dir_to_gcs(local_dir: str, bucket_name: str) -> str:
        import uuid

        from google.cloud import storage

        prefix = f"vertex-ai-staging/model-uploads/{uuid.uuid4().hex}/"
        client = storage.Client(project=project_id)
        b = client.bucket(bucket_name)
        for fn in os.listdir(local_dir):
            fp = os.path.join(local_dir, fn)
            if os.path.isfile(fp):
                b.blob(prefix + fn).upload_from_filename(fp)
        return f"gs://{bucket_name}/{prefix}"

    with tempfile.TemporaryDirectory() as td:
        # sklearn-cpu upload validates filenames; must be model.joblib (not delay_model.joblib).
        mp = os.path.join(td, "model.joblib")
        joblib.dump(artifact, mp)
        if not os.path.isfile(mp) or os.path.getsize(mp) < 1:
            raise RuntimeError("[delay] model.joblib missing or empty after dump")
        gcs_artifact_uri = _sync_model_dir_to_gcs(td, _bucket)

        try:
            uploaded = aiplatform.Model.upload(
                display_name="ecom-delay-risk-model",
                artifact_uri=gcs_artifact_uri,
                serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-5:latest",
                labels={"task": "delay_risk", "framework": "xgboost"},
            )
        except Exception as exc:
            raise RuntimeError(
                f"[delay] Model.upload failed (project={project_id}, region={region}): {exc}"
            ) from exc

    rid = uploaded.resource_name
    with open(delay_model_resource_path, "w", encoding="utf-8") as f:
        f.write(rid)
    with open(delay_metric_path, "w", encoding="utf-8") as f:
        f.write(str(metric_value))


@dsl.component(
    base_image="python:3.11",
    packages_to_install=[
        "numpy==2.1.3",
        "snowflake-connector-python==4.4.0",
    ],
)
def drift_registry_vertex_component(
    project_id: str,
    region: str,
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
    forecast_model_in: dsl.InputPath(),
    forecast_metric_in: dsl.InputPath(),
    delay_model_in: dsl.InputPath(),
    delay_metric_in: dsl.InputPath(),
) -> None:
    _ = project_id, region

    import numpy as np
    import snowflake.connector

    def read_text(path: str) -> str:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()

    forecast_model_resource = read_text(forecast_model_in)
    delay_model_resource = read_text(delay_model_in)
    forecast_metric_value_f = float(read_text(forecast_metric_in))
    delay_metric_value_f = float(read_text(delay_metric_in))

    def psi(expected, actual, bins=10):
        expected = np.asarray(expected, dtype=float)
        actual = np.asarray(actual, dtype=float)
        eps = 1e-6
        quantiles = np.linspace(0, 1, bins + 1)
        cuts = np.quantile(expected, quantiles)
        cuts = np.unique(cuts)
        if len(cuts) < 3:
            return 0.0
        e_hist, _ = np.histogram(expected, bins=cuts)
        a_hist, _ = np.histogram(actual, bins=cuts)
        e_dist = np.clip(e_hist / max(e_hist.sum(), 1), eps, None)
        a_dist = np.clip(a_hist / max(a_hist.sum(), 1), eps, None)
        return float(np.sum((a_dist - e_dist) * np.log(a_dist / e_dist)))

    def payment_values_from_cursor(c):
        rows = c.fetchall()
        out = []
        for r in rows:
            v = r[0]
            if v is None:
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
        return np.asarray(out, dtype=float)

    conn = None
    cur = None
    try:
        conn = snowflake.connector.connect(
            account=snowflake_account,
            user=snowflake_user,
            password=snowflake_password,
            warehouse=snowflake_warehouse,
            database=snowflake_database,
            schema=snowflake_schema,
            role=snowflake_role or None,
        )
        cur = conn.cursor()

        cur.execute(
            "SELECT payment_value FROM ECOM_ANALYTICS.FEATURES.DRIFT_BASELINE_SAMPLE LIMIT 100000"
        )
        baseline = payment_values_from_cursor(cur)
        cur.execute(
            "SELECT payment_value FROM ECOM_ANALYTICS.FEATURES.DRIFT_CURRENT_SAMPLE LIMIT 100000"
        )
        current = payment_values_from_cursor(cur)
        drift_score = psi(baseline, current) if len(baseline) and len(current) else 0.0
        if not np.isfinite(drift_score):
            drift_score = 0.0
        drift_flag = drift_score >= 0.25

        cur.execute(
            """
            SELECT model_resource_name, metric_value
            FROM ECOM_ANALYTICS.MART.MODEL_REGISTRY
            WHERE model_type='forecast' AND is_champion=TRUE
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        old_forecast = cur.fetchone()
        old_forecast_model = old_forecast[0] if old_forecast else None
        if old_forecast is None or old_forecast[1] is None:
            old_forecast_metric = 9999.0
        else:
            old_forecast_metric = float(old_forecast[1])
        forecast_is_better = forecast_metric_value_f < old_forecast_metric

        cur.execute(
            """
            SELECT model_resource_name, metric_value
            FROM ECOM_ANALYTICS.MART.MODEL_REGISTRY
            WHERE model_type='delay_risk' AND is_champion=TRUE
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        old_delay = cur.fetchone()
        old_delay_model = old_delay[0] if old_delay else None
        if old_delay is None or old_delay[1] is None:
            old_delay_metric = -1.0
        else:
            old_delay_metric = float(old_delay[1])
        delay_is_better = delay_metric_value_f > old_delay_metric

        forecast_champion = old_forecast_model or forecast_model_resource
        delay_champion = old_delay_model or delay_model_resource

        if not drift_flag and forecast_is_better:
            cur.execute(
                "UPDATE ECOM_ANALYTICS.MART.MODEL_REGISTRY SET is_champion=FALSE WHERE model_type='forecast' AND is_champion=TRUE"
            )
            cur.execute(
                """
                INSERT INTO ECOM_ANALYTICS.MART.MODEL_REGISTRY
                (model_type, model_resource_name, metric_name, metric_value, is_champion, drift_score, updated_at)
                VALUES ('forecast', %s, 'MAPE', %s, TRUE, %s, CURRENT_TIMESTAMP())
                """,
                (forecast_model_resource, forecast_metric_value_f, drift_score),
            )
            forecast_champion = forecast_model_resource

        if not drift_flag and delay_is_better:
            cur.execute(
                "UPDATE ECOM_ANALYTICS.MART.MODEL_REGISTRY SET is_champion=FALSE WHERE model_type='delay_risk' AND is_champion=TRUE"
            )
            cur.execute(
                """
                INSERT INTO ECOM_ANALYTICS.MART.MODEL_REGISTRY
                (model_type, model_resource_name, metric_name, metric_value, is_champion, drift_score, updated_at)
                VALUES ('delay_risk', %s, 'ROC_AUC', %s, TRUE, %s, CURRENT_TIMESTAMP())
                """,
                (delay_model_resource, delay_metric_value_f, drift_score),
            )
            delay_champion = delay_model_resource

        cur.execute(
            """
            INSERT INTO ECOM_ANALYTICS.MART.DRIFT_AUDIT
            (run_ts, feature_name, psi_score, drift_flag)
            VALUES (CURRENT_TIMESTAMP(), 'payment_value', %s, %s)
            """,
            (drift_score, drift_flag),
        )
        conn.commit()
    except Exception as exc:
        raise RuntimeError(f"[drift] Snowflake/registry step failed: {exc}") from exc
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
