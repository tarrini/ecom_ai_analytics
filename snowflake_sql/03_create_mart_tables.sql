CREATE SCHEMA IF NOT EXISTS ECOM_ANALYTICS.MART;


CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.fct_orders AS
WITH item_totals AS (
    SELECT order_id, SUM(price) AS items_total, SUM(freight_value) AS freight_total
    FROM ECOM_ANALYTICS.staging.stg_order_items
    GROUP BY order_id
),
payment_totals AS (
    SELECT order_id, SUM(payment_value) AS payment_total
    FROM ECOM_ANALYTICS.staging.stg_payments
    GROUP BY order_id
),
review_scores AS (
    SELECT order_id, AVG(review_score) AS avg_review_score
    FROM ECOM_ANALYTICS.staging.stg_reviews
    GROUP BY order_id
),
geo_dedup AS (
    SELECT
        geolocation_zip_code_prefix,
        AVG(geolocation_lat) AS geolocation_lat,
        AVG(geolocation_lng) AS geolocation_lng
    FROM ECOM_ANALYTICS.staging.stg_geolocation
    GROUP BY geolocation_zip_code_prefix
)
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    c.customer_city,
    c.customer_state,
    gd.geolocation_lat AS customer_lat,
    gd.geolocation_lng AS customer_lng,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    it.items_total,
    it.freight_total,
    pt.payment_total,
    rs.avg_review_score,
    CASE
        WHEN o.order_delivered_customer_date IS NULL OR o.order_estimated_delivery_date IS NULL THEN NULL
        WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date THEN 1
        ELSE 0
    END AS is_delayed,
    DATE_TRUNC('day', o.order_purchase_timestamp) AS order_date
FROM ECOM_ANALYTICS.STAGING.stg_orders o
LEFT JOIN ECOM_ANALYTICS.STAGING.stg_customer c
    ON o.customer_id = c.customer_id
LEFT JOIN geo_dedup gd
    ON c.customer_zip_code_prefix = gd.geolocation_zip_code_prefix
LEFT JOIN item_totals it
    ON o.order_id = it.order_id
LEFT JOIN payment_totals pt
    ON o.order_id = pt.order_id
LEFT JOIN review_scores rs
    ON o.order_id = rs.order_id;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.daily_kpi AS
SELECT
    order_date AS kpi_date,
    COUNT(DISTINCT order_id) AS total_orders,
    COALESCE(SUM(payment_total), 0) AS gmv,
    CASE WHEN COUNT(DISTINCT order_id) = 0 THEN 0
         ELSE COALESCE(SUM(payment_total), 0) / COUNT(DISTINCT order_id)
    END AS aov,
    AVG(avg_review_score) AS avg_review_score,
    AVG(is_delayed::FLOAT) * 100 AS delayed_order_pct
FROM ECOM_ANALYTICS.MART.fct_orders
WHERE order_date IS NOT NULL
GROUP BY order_date;


CREATE OR REPLACE VIEW ECOM_ANALYTICS.MART.vw_exec_dashboard AS
WITH base AS (
    SELECT
        f.order_date AS kpi_date,
        f.customer_state,
        COUNT(DISTINCT f.order_id) AS total_orders,
        COALESCE(SUM(f.payment_total), 0) AS gmv,
        CASE
            WHEN COUNT(DISTINCT f.order_id) = 0 THEN 0
            ELSE COALESCE(SUM(f.payment_total), 0) / COUNT(DISTINCT f.order_id)
        END AS aov,
        AVG(f.avg_review_score) AS avg_review_score,
        AVG(f.is_delayed::FLOAT) * 100 AS delayed_order_pct
    FROM ECOM_ANALYTICS.MART.FCT_ORDERS f
    WHERE f.order_date IS NOT NULL
      AND f.customer_state IS NOT NULL
    GROUP BY f.order_date, f.customer_state
)
SELECT
    b.kpi_date,
    b.customer_state,
    b.total_orders,
    b.gmv,
    b.aov,
    b.avg_review_score,
    b.delayed_order_pct,
    LAG(b.gmv) OVER (PARTITION BY b.customer_state ORDER BY b.kpi_date) AS prev_day_gmv,
    LAG(b.total_orders) OVER (PARTITION BY b.customer_state ORDER BY b.kpi_date) AS prev_day_orders,
    LAG(b.aov) OVER (PARTITION BY b.customer_state ORDER BY b.kpi_date) AS prev_day_aov,
    LAG(b.delayed_order_pct) OVER (PARTITION BY b.customer_state ORDER BY b.kpi_date) AS prev_day_delayed_pct,
    LAG(b.avg_review_score) OVER (PARTITION BY b.customer_state ORDER BY b.kpi_date) AS prev_day_avg_review_score
FROM base b;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.customer_repeat_metrics AS
WITH cust_orders AS (
    SELECT
        customer_unique_id,
        COUNT(DISTINCT order_id) AS order_count
    FROM ECOM_ANALYTICS.MART.fct_orders
    WHERE customer_unique_id IS NOT NULL
    GROUP BY customer_unique_id
)
SELECT
    COUNT(*) AS total_customers,
    COUNT_IF(order_count > 1) AS repeat_customers,
    CASE WHEN COUNT(*) = 0 THEN 0
         ELSE COUNT_IF(order_count > 1) * 100.0 / COUNT(*)
    END AS repeat_customer_pct
FROM cust_orders;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.MART.geo_kpi AS
SELECT
    customer_state,
    customer_city,
    ROUND(customer_lat, 4) AS customer_lat,
    ROUND(customer_lng, 4) AS customer_lng,
    COUNT(DISTINCT order_id) AS total_orders,
    SUM(payment_total) AS gmv,
    AVG(is_delayed::FLOAT) * 100 AS delayed_order_pct
FROM ECOM_ANALYTICS.MART.fct_orders
WHERE customer_lat IS NOT NULL
  AND customer_lng IS NOT NULL
GROUP BY customer_state, customer_city, ROUND(customer_lat, 4), ROUND(customer_lng, 4);