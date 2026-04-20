
from __future__ import annotations

import json
import os
import sys
import uuid

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env", override=True)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _as_bool(name: str, default: str = "true") -> bool:
    return _env(name, default).lower() in {"1", "true", "yes", "y"}


def main() -> None:
    if not _as_bool("RUN_LLM_BRIEF", "true"):
        print("RUN_LLM_BRIEF is false — skipping LLM brief.")
        return

    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Missing OPENAI_API_KEY")

    import snowflake.connector
    from openai import OpenAI

    conn = snowflake.connector.connect(
        account=_env("SNOWFLAKE_ACCOUNT"),
        user=_env("SNOWFLAKE_USER"),
        password=_env("SNOWFLAKE_PASSWORD"),
        warehouse=_env("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=_env("SNOWFLAKE_DATABASE", "ECOM_ANALYTICS"),
        schema=_env("SNOWFLAKE_SCHEMA", "RAW"),
        role=_env("SNOWFLAKE_ROLE") or None,
    )
    cur = conn.cursor()

    cur.execute(
        """
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
        """
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("No DAILY_KPI rows for brief.")

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
    model = _env("OPENAI_MODEL", "gpt-4o-mini")

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    summary = response.output_text.strip()

    run_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO ECOM_ANALYTICS.MART.CLIENT_DAILY_BRIEF
        (brief_date, generated_at, kpi_json, ai_summary, channel, run_id)
        SELECT CURRENT_DATE(), CURRENT_TIMESTAMP(), PARSE_JSON(%s), %s, %s, %s
        """,
        (json.dumps(payload), summary, "automated_pipeline", run_id),
    )
    conn.commit()
    cur.close()
    conn.close()

    print("Daily client brief generated and stored.")
    print(summary[:500] + ("…" if len(summary) > 500 else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Brief failed: {exc}", file=sys.stderr)
        sys.exit(1)
