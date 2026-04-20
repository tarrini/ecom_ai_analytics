import os
from pathlib import Path
from dotenv import load_dotenv
import snowflake.connector

load_dotenv()

sf_account = os.getenv("SNOWFLAKE_ACCOUNT")
sf_user = os.getenv("SNOWFLAKE_USER")
sf_password = os.getenv("SNOWFLAKE_PASSWORD")
sf_warehouse = os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
sf_database = os.getenv("SNOWFLAKE_DATABASE", "ECOM_ANALYTICS")
sf_schema = os.getenv("SNOWFLAKE_SCHEMA", "RAW")
sf_role = os.getenv("SNOWFLAKE_ROLE")

TASKS = [
    
    "ECOM_ANALYTICS.OPS.TASK_REFRESH_DAILY_KPI",
    "ECOM_ANALYTICS.OPS.TASK_REFRESH_GEO_KPI",
    "ECOM_ANALYTICS.OPS.TASK_REFRESH_CUSTOMER_REPEAT_METRICS",
    "ECOM_ANALYTICS.OPS.TASK_REFRESH_MART",
    "ECOM_ANALYTICS.OPS.TASK_STG_GEOLOCATION",
    "ECOM_ANALYTICS.OPS.TASK_STG_SELLERS",
    "ECOM_ANALYTICS.OPS.TASK_STG_REVIEWS",
    "ECOM_ANALYTICS.OPS.TASK_STG_PRODUCTS",
    "ECOM_ANALYTICS.OPS.TASK_STG_CUSTOMERS",
    "ECOM_ANALYTICS.OPS.TASK_STG_PAYMENTS",
    "ECOM_ANALYTICS.OPS.TASK_STG_ORDER_ITEMS",
    "ECOM_ANALYTICS.OPS.TASK_STG_ORDERS",
    "ECOM_ANALYTICS.OPS.TASK_START",
]

def get_conn():
    return snowflake.connector.connect(
        account=sf_account,
        user=sf_user,
        password=sf_password,
        warehouse=sf_warehouse,
        database=sf_database,
        schema=sf_schema,
        role=sf_role,
    )

def run_sql_file(cur, path: str):
    sql_text = Path(path).read_text(encoding="utf-8")
    statements = [s.strip() for s in sql_text.split(";") if s.strip()]
    for stmt in statements:
        cur.execute(stmt)

def main():
    conn = get_conn()
    cur = conn.cursor()
    try:
        print("Creating STAGING tables...")
        run_sql_file(cur, "snowflake_sql/02_create_stage_tables.sql")

        print("Creating MART tables...")
        run_sql_file(cur, "snowflake_sql/03_create_mart_tables.sql")

        print("Creating Streams + Tasks...")
        run_sql_file(cur, "snowflake_sql/04_create_streams_tasks.sql")

        print("Resuming tasks...")
        for task in TASKS:
            cur.execute(f"ALTER TASK {task} RESUME")
            print(f"Resumed: {task}")

        # Run root task manually once for immediate refresh
        cur.execute("EXECUTE TASK ECOM_ANALYTICS.OPS.TASK_START")
        print("Triggered root task once: TASK_REFRESH_STAGING")

        # quick checks
        print("\nValidation counts:")
        cur.execute("SELECT COUNT(*) FROM ECOM_ANALYTICS.MART.FCT_ORDERS")
        print("FCT_ORDERS:", cur.fetchone()[0])

        cur.execute("SELECT COUNT(*) FROM ECOM_ANALYTICS.MART.DAILY_KPI")
        print("DAILY_KPI:", cur.fetchone()[0])

        print("\nsnowflake setup completed successfully.")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()