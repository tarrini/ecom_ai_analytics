import os
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import storage
import snowflake.connector

load_dotenv()
raw_dir=Path("data/raw")
gcs_bucket=os.getenv("GCS_BUCKET")
gcs_prefix=os.getenv("GCS_PREFIX","bronze/olist")
sf_account=os.getenv("SNOWFLAKE_ACCOUNT")
sf_user=os.getenv("SNOWFLAKE_USER")
sf_password=os.getenv("SNOWFLAKE_PASSWORD")
sf_warehouse=os.getenv("SNOWFLAKE_WAREHOUSE")
sf_database=os.getenv("SNOWFLAKE_DATABASE")
sf_schema=os.getenv("SNOWFLAKE_SCHEMA")
sf_role=os.getenv("SNOWFLAKE_ROLE")

raw_table= {
    "raw_customers": "olist_customers_dataset.csv",
    "raw_geolocation":"olist_geolocation_dataset.csv",
    "raw_orders": "olist_orders_dataset.csv",
    "raw_order_items": "olist_order_items_dataset.csv",
    "raw_payments": "olist_order_payments_dataset.csv",
    "raw_products": "olist_products_dataset.csv",
    "raw_reviews": "olist_order_reviews_dataset.csv",
    "raw_sellers": "olist_sellers_dataset.csv",
}
def upload_to_gcs():
    if not gcs_bucket:
        raise ValueError("No bucket available in .env")
    csv_files=sorted(raw_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError("No csvs found.Run ingest script first")
    client=storage.Client()
    bucket=client.bucket(gcs_bucket)

    for files in csv_files:
        blob_name=f"{gcs_prefix}/{files.name}"
        blob=bucket.blob(blob_name)
        blob.upload_from_filename(str(files))
        print(f"Uploaded: gs://{gcs_bucket}/{blob_name}")
    print(f"Uploaded {len(csv_files)} files to GCS.")

def sf_connect():
    missing=[k for k,v in {
        "SNOWFLAKE_ACCOUNT":sf_account,
        "SNOWFLAKE_USER":sf_user,
        "SNOWFLAKE_PASSWORD":sf_password,
        "SNOWFLAKE_WAREHOUSE":sf_warehouse,
    }.items()if not v]
    if missing:
        raise ValueError(f"The value is missing {', '.join(missing)}")
    return snowflake.connector.connect(
         account=sf_account,
        user=sf_user,
        password=sf_password,
        warehouse=sf_warehouse,
        database=sf_database,
        schema=sf_schema,
        role=sf_role,
    )
    
def run_sql_file(cur,path):
    sql_text=Path(path).read_text(encoding="utf-8")
    statements=[s.strip() for s in sql_text.split(";") if s.strip()]
    for stmt in statements:
        cur.execute(stmt)
def create_staging_bucket(cur):
    staging=f''' create or replace stage {sf_database}.{sf_schema}.olist_stage
    url='gcs://{gcs_bucket}/{gcs_prefix}/'
    storage_integration=gcs_int_ecom
    file_format={sf_database}.{sf_schema}.csv_fmt'''
    cur.execute(staging)
    print(f"Stage created: gcs://{gcs_bucket}/{gcs_prefix}/")

def copy_into_rawtables(cur):
    for table,file_name in raw_table.items() :
        copy_data=f'''copy into {sf_database}.{sf_schema}.{table}
        from @{sf_database}.{sf_schema}.olist_stage/{file_name}
        file_format={sf_database}.{sf_schema}.csv_fmt
        on_error=continue'''
        cur.execute(copy_data)
        print(f"COPY completed: {table} <- {file_name}")
def print_count(cur):
    for table in raw_table:
        cur.execute(f"select count(*) from {sf_database}.{sf_schema}.{table} ")
        count=cur.fetchone()[0]
        print(f"{table}:{count}rows")

def main():
    print("1.Uploading files to gcs")
    upload_to_gcs()
    print("2.Creating snowflake connection")
    conn=sf_connect()
    cur=conn.cursor()
    try:
        run_sql_file(cur,"sql/01_create_raw_tables.sql") 
        create_staging_bucket(cur)
        print("3.Loading raw tables from gcs")
        copy_into_rawtables(cur)
        print_count(cur)
    finally:
        cur.close()
        conn.close()
    print("GCS -> Snowflake load completed.")
if __name__ == "__main__":
    main()
