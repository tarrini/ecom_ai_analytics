-- One view for Tableau Executive: state × day + prior-day columns for DoD.
-- Requires MART.FCT_ORDERS (from snowflake_sql/03 or 04 pipeline).

CREATE SCHEMA IF NOT EXISTS ECOM_ANALYTICS.MART;

CREATE OR REPLACE VIEW ECOM_ANALYTICS.MART.VW_EXEC_DASHBOARD AS
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
