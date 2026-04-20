create SCHEMA if not EXISTS ECOM_ANALYTICS.staging;

create or REPLACE TABLE ECOM_ANALYTICS.staging.stg_customer as 
select customer_id,customer_unique_id,customer_zip_code_prefix,customer_city,customer_state
from ECOM_ANALYTICS.RAW.RAW_CUSTOMERS;

create or replace table ECOM_ANALYTICS.staging.stg_orders as 
select order_id,customer_id,order_status,
try_to_timestamp_ntz(order_purchase_timestamp) as order_purchase_timestamp,
try_to_timestamp_ntz(order_approved_at)as order_approved_at,
try_to_timestamp_ntz(order_delivered_carrier_date)as order_delivered_carrier_date,
try_to_timestamp_ntz(order_delivered_customer_date)as order_delivered_customer_date,
try_to_timestamp_ntz(order_estimated_delivery_date)as order_estimated_delivery_date
from ECOM_ANALYTICS.RAW.RAW_ORDERS;

create or replace table ECOM_ANALYTICS.staging.stg_order_items as
select order_id,try_to_number(order_item_id)as order_item_id,
product_id,seller_id,
try_to_timestamp_ntz(shipping_limit_date)as shipping_limit_date,
try_to_decimal(price,10,2)as price ,
try_to_decimal(freight_value,10,2)as freight_value
from ECOM_ANALYTICS.RAW.RAW_ORDER_ITEMS;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.staging.stg_payments AS
SELECT
    order_id,
    TRY_TO_NUMBER(payment_sequential) AS payment_sequential,
    payment_type,
    TRY_TO_NUMBER(payment_installments) AS payment_installments,
    TRY_TO_DECIMAL(payment_value, 18, 2) AS payment_value
FROM ECOM_ANALYTICS.RAW.RAW_PAYMENTS;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.staging.stg_products AS
SELECT
    product_id,
    product_category_name,
    TRY_TO_NUMBER(product_weight_g) AS product_weight_g,
    TRY_TO_NUMBER(product_length_cm) AS product_length_cm,
    TRY_TO_NUMBER(product_height_cm) AS product_height_cm,
    TRY_TO_NUMBER(product_width_cm) AS product_width_cm
FROM ECOM_ANALYTICS.RAW.RAW_PRODUCTS;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.staging.stg_reviews AS
SELECT
    review_id,
    order_id,
    TRY_TO_NUMBER(review_score) AS review_score,
    review_comment_title,
    review_comment_message,
    TRY_TO_DATE(review_creation_date) AS review_creation_date,
    TRY_TO_TIMESTAMP_NTZ(review_answer_timestamp) AS review_answer_ts
FROM ECOM_ANALYTICS.RAW.RAW_REVIEWS;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.staging.stg_sellers AS
SELECT
    seller_id,
    seller_zip_code_prefix,
    seller_city,
    seller_state
FROM ECOM_ANALYTICS.RAW.RAW_SELLERS;

CREATE OR REPLACE TABLE ECOM_ANALYTICS.staging.stg_geolocation AS
SELECT
    geolocation_zip_code_prefix,
    TRY_TO_DOUBLE(geolocation_lat) AS geolocation_lat,
    TRY_TO_DOUBLE(geolocation_lng) AS geolocation_lng,
    geolocation_city,
    geolocation_state
FROM ECOM_ANALYTICS.RAW.RAW_GEOLOCATION;