from kfp import dsl


@dsl.component(
    base_image="python:3.11",
    packages_to_install=[
        "snowflake-connector-python==4.4.0",
        "openai==2.32.0",
    ],
)
def llm_daily_brief_op(
    snowflake_account: str,
    snowflake_user: str,
    snowflake_password: str,
    snowflake_warehouse: str,
    snowflake_database: str,
    snowflake_schema: str,
    snowflake_role: str,
    openai_api_key: str,
    openai_model: str = "gpt-4o-mini",
) -> None:
    import json
    import snowflake.connector
    from openai import OpenAI

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

    cur.execute("""
        WITH d AS (
          SELECT kpi_date, total_orders, gmv, aov, avg_review_score, delayed_order_pct,
                 ROW_NUMBER() OVER (ORDER BY kpi_date DESC) rn
          FROM ECOM_ANALYTICS.MART.DAILY_KPI
        )
        SELECT
          d1.kpi_date,
          d1.total_orders, d2.total_orders,
          d1.gmv, d2.gmv,
          d1.aov, d2.aov,
          d1.avg_review_score, d2.avg_review_score,
          d1.delayed_order_pct, d2.delayed_order_pct
        FROM d d1 LEFT JOIN d d2 ON d2.rn=2
        WHERE d1.rn=1
    """)
    row = cur.fetchone()

    payload = {
        "kpi_date": str(row[0]),
        "orders_latest": float(row[1] or 0),
        "orders_prev": float(row[2] or 0),
        "gmv_latest": float(row[3] or 0),
        "gmv_prev": float(row[4] or 0),
        "aov_latest": float(row[5] or 0),
        "aov_prev": float(row[6] or 0),
        "review_latest": float(row[7] or 0),
        "review_prev": float(row[8] or 0),
        "delay_latest": float(row[9] or 0),
        "delay_prev": float(row[10] or 0),
    }

    system_prompt = """
You are a senior e-commerce analytics consultant.
Rules:
1) Use ONLY provided KPI data. No fabricated numbers.
2) Be concise, business-safe, non-alarming.
3) Mention positive changes, negative changes, and 3 actions.
4) If data is insufficient, explicitly say so.
5) Avoid legal, medical, financial compliance advice.
Output sections:
- Performance Snapshot
- What Improved
- What Needs Attention
- Recommended Actions (3 bullets)
"""

    user_prompt = f"KPI JSON:\n{json.dumps(payload, indent=2)}"

    client = OpenAI(api_key=openai_api_key)
    response = client.responses.create(
        model=openai_model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    summary = response.output_text.strip()

    cur.execute(
        """
        INSERT INTO ECOM_ANALYTICS.MART.CLIENT_DAILY_BRIEF
        (brief_date, generated_at, kpi_json, ai_summary, channel, run_id)
        SELECT CURRENT_DATE(), CURRENT_TIMESTAMP(), PARSE_JSON(%s), %s, 'email_slack', UUID_STRING()
        """,
        (json.dumps(payload), summary),
    )
    conn.commit()
    cur.close()
    conn.close()