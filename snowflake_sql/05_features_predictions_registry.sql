CREATE SCHEMA IF NOT EXISTS ECOM_ANALYTICS.FEATURES;
CREATE SCHEMA IF NOT EXISTS ECOM_ANALYTICS.MART;


CREATE OR REPLACE TABLE ECOM_ANALYTICS.FEATURES.FCT_DEMAND_FEATURES_TRAIN AS
WITH base AS (
  SELECT
    order_date::DATE AS kpi_date,
    customer_state,
    COALESCE(product_category_name, 'unknown') AS product_category_name,
    COUNT(DISTINCT order_id) AS daily_orders,
    SUM(payment_total) AS daily_gmv,
    AVG(avg_review_score) AS daily_review
  FROM ECOM_ANALYTICS.MART.FCT_ORDERS
  LEFT JOIN ECOM_ANALYTICS.STAGING.STG_ORDER_ITEMS i USING(order_id)
  LEFT JOIN ECOM_ANALYTICS.STAGING.STG_PRODUCTS p USING(product_id)
  WHERE order_date IS NOT NULL
  GROUP BY 1,2,3
),
feat AS (
  SELECT
    *,
    LAG(daily_orders,1) OVER (PARTITION BY customer_state, product_category_name ORDER BY kpi_date) AS lag_1_orders,
    LAG(daily_orders,7) OVER (PARTITION BY customer_state, product_category_name ORDER BY kpi_date) AS lag_7_orders,
    AVG(daily_orders) OVER (
      PARTITION BY customer_state, product_category_name ORDER BY kpi_date
      ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
    ) AS rolling_7_orders,
    DAYOFWEEK(kpi_date) AS dow,
    MONTH(kpi_date) AS month_num,
    LEAD(daily_orders,7) OVER (PARTITION BY customer_state, product_category_name ORDER BY kpi_date) AS target_orders_next_7d
  FROM base
)
SELECT * FROM feat
WHERE target_orders_next_7d IS NOT NULL;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.FEATURES.FCT_DEMAND_FEATURES_SCORING AS
SELECT
  kpi_date AS forecast_date,
  customer_state,
  product_category_name,
  daily_orders,
  daily_gmv,
  daily_review,
  lag_1_orders,
  lag_7_orders,
  rolling_7_orders,
  dow,
  month_num
FROM ECOM_ANALYTICS.FEATURES.FCT_DEMAND_FEATURES_TRAIN
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_state, product_category_name ORDER BY kpi_date DESC)=1;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.FEATURES.FCT_DELAY_FEATURES_TRAIN AS
SELECT
  f.order_id,
  f.order_purchase_ts,
  f.customer_state,
  s.seller_state,
  p.payment_type,
  COALESCE(i.price,0) AS item_price,
  COALESCE(i.freight_value,0) AS freight_value,
  DATEDIFF('day', f.order_purchase_ts, f.order_delivered_customer_ts) AS delivery_days,
  IFF(f.is_delayed = 1, 1, 0) AS is_delayed
FROM ECOM_ANALYTICS.MART.FCT_ORDERS f
LEFT JOIN ECOM_ANALYTICS.STAGING.STG_ORDER_ITEMS i USING(order_id)
LEFT JOIN ECOM_ANALYTICS.STAGING.STG_SELLERS s USING(seller_id)
LEFT JOIN ECOM_ANALYTICS.STAGING.STG_PAYMENTS p USING(order_id)
WHERE f.order_purchase_ts IS NOT NULL;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.FEATURES.FCT_DELAY_FEATURES_SCORING AS
SELECT
  order_id,
  order_purchase_ts,
  customer_state,
  seller_state,
  payment_type,
  item_price,
  freight_value,
  delivery_days
FROM ECOM_ANALYTICS.FEATURES.FCT_DELAY_FEATURES_TRAIN
QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY order_purchase_ts DESC)=1;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.DEMAND_FORECAST_DAILY (
  forecast_date DATE,
  customer_state STRING,
  product_category_name STRING,
  predicted_orders_7d FLOAT,
  model_version STRING,
  scored_at TIMESTAMP_NTZ
);

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.DELAY_RISK_SCORES (
  order_id STRING,
  order_purchase_ts TIMESTAMP_NTZ,
  customer_state STRING,
  seller_state STRING,
  payment_type STRING,
  delay_risk_score FLOAT,
  model_version STRING,
  scored_at TIMESTAMP_NTZ
);

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.MODEL_REGISTRY (
  model_type STRING,             -- forecast / delay_risk
  model_resource_name STRING,
  metric_name STRING,            -- MAPE / ROC_AUC
  metric_value FLOAT,
  is_champion BOOLEAN,
  drift_score FLOAT,
  updated_at TIMESTAMP_NTZ
);

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.DRIFT_AUDIT (
  run_ts TIMESTAMP_NTZ,
  feature_name STRING,
  psi_score FLOAT,
  drift_flag BOOLEAN
);

CREATE OR REPLACE TABLE ECOM_ANALYTICS.FEATURES.DRIFT_BASELINE_SAMPLE AS
SELECT payment_value
FROM ECOM_ANALYTICS.STAGING.STG_PAYMENTS
WHERE payment_value IS NOT NULL
LIMIT 200000;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.FEATURES.DRIFT_CURRENT_SAMPLE AS
SELECT payment_value
FROM ECOM_ANALYTICS.STAGING.STG_PAYMENTS
WHERE payment_value IS NOT NULL
LIMIT 200000;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.CLIENT_DAILY_BRIEF (
  brief_date DATE,
  generated_at TIMESTAMP_NTZ,
  kpi_json VARIANT,
  ai_summary STRING,
  channel STRING,
  run_id STRING
);