
from kfp import dsl


@dsl.component(
    base_image="python:3.11",
    packages_to_install=[
        "pandas==2.2.3",
        "numpy==2.1.3",
        "snowflake-connector-python==4.4.0",
        "google-cloud-aiplatform>=1.38.0",
        "scikit-learn==1.5.2",
        "xgboost==2.1.1",
        "joblib==1.4.2",
    ],
)
def train_forecast_vertex_component(
    project_id: str,
    region: str,
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
    from xgboost import XGBRegressor

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
    cur.execute("SELECT * FROM ECOM_ANALYTICS.FEATURES.FCT_DEMAND_FEATURES_TRAIN")
    df = cur.fetch_pandas_all()
    cur.close()
    conn.close()

    if df.empty:
        raise RuntimeError("No training rows in FEATURES.FCT_DEMAND_FEATURES_TRAIN")

    target = "target_orders_next_7d"
    feature_cols = [
        c
        for c in df.columns
        if c not in ["kpi_date", "customer_state", "product_category_name", target]
    ]
    df = df.sort_values("kpi_date").dropna(subset=[target])

    X = df[feature_cols].fillna(0)
    y = df[target].astype(float)

    tscv = TimeSeriesSplit(n_splits=4)
    scores = []
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
            random_state=42,
        )
        model.fit(xtr, ytr)
        pred = model.predict(xva)
        mape = mean_absolute_percentage_error(yva, np.maximum(pred, 1e-8))
        scores.append(mape)
        best_model = model

    metric_value = float(np.mean(scores))

    aiplatform.init(project=project_id, location=region)

    with tempfile.TemporaryDirectory() as td:
        model_path = os.path.join(td, "forecast_model.joblib")
        joblib.dump({"model": best_model, "features": feature_cols}, model_path)

        uploaded = aiplatform.Model.upload(
            display_name="ecom-forecast-model",
            artifact_uri=td,
            serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-5:latest",
            labels={"task": "forecast", "framework": "xgboost"},
        )

    rid = uploaded.resource_name
    with open(forecast_model_resource_path, "w", encoding="utf-8") as f:
        f.write(rid)
    with open(forecast_metric_path, "w", encoding="utf-8") as f:
        f.write(str(metric_value))


@dsl.component(
    base_image="python:3.11",
    packages_to_install=[
        "pandas==2.2.3",
        "numpy==2.1.3",
        "snowflake-connector-python==4.4.0",
        "google-cloud-aiplatform>=1.38.0",
        "scikit-learn==1.5.2",
        "xgboost==2.1.1",
        "joblib==1.4.2",
    ],
)
def train_delay_vertex_component(
    project_id: str,
    region: str,
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
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold
    from xgboost import XGBClassifier

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
    auc_scores = []
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

    rid = uploaded.resource_name
    with open(delay_model_resource_path, "w", encoding="utf-8") as f:
        f.write(rid)
    with open(delay_metric_path, "w", encoding="utf-8") as f:
        f.write(str(metric_value))


@dsl.component(
    base_image="python:3.11",
    packages_to_install=[
        "pandas==2.2.3",
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
    baseline = cur.fetch_pandas_all().iloc[:, 0].dropna().values
    cur.execute(
        "SELECT payment_value FROM ECOM_ANALYTICS.FEATURES.DRIFT_CURRENT_SAMPLE LIMIT 100000"
    )
    current = cur.fetch_pandas_all().iloc[:, 0].dropna().values
    drift_score = psi(baseline, current) if len(baseline) and len(current) else 0.0
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
    old_forecast_metric = float(old_forecast[1]) if old_forecast else 9999.0
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
    old_delay_metric = float(old_delay[1]) if old_delay else -1.0
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
    cur.close()
    conn.close()
