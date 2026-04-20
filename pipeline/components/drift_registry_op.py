

from __future__ import annotations

import os
from typing import Tuple

import numpy as np
import snowflake.connector


def run_drift_and_registry_op(
    project_id: str,
    region: str,
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
    forecast_model_resource: str,
    forecast_metric_value: float,
    delay_model_resource: str,
    delay_metric_value: float,
) -> Tuple[str, str]:
    _ = project_id, region  # reserved for future logging / metadata

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
        role=snowflake_role,
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
    forecast_is_better = forecast_metric_value < old_forecast_metric

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
    delay_is_better = delay_metric_value > old_delay_metric

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
            (forecast_model_resource, forecast_metric_value, drift_score),
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
            (delay_model_resource, delay_metric_value, drift_score),
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

    return forecast_champion or "", delay_champion or ""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    print(
        "Run drift/registry via pipeline/run_ml_local.py after training, or pass args here."
    )
