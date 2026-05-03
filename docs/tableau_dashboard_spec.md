# Tableau Cloud — advanced workbook (existing Snowflake tables only)

Use **marts** from `snowflake_sql/03_*` / `04_*` / `05_*`. Optional view for Tableau Executive: **`snowflake_sql/06_vw_exec_dashboard.sql`** (`vw_exec_dashboard`).

Workbooks are built and saved in **Tableau Cloud**. This file is the blueprint only.

---

## Executive: use your `vw_exec_dashboard` (recommended)

If you **already created** `ECOM_ANALYTICS.MART.vw_exec_dashboard` in Snowflake, use it as **`DS_EXEC_STATE`** for the Executive tab. It is **pre-aggregated** (state × day), so extracts are **smaller and faster** than scanning all of `FCT_ORDERS`.

Typical columns: `kpi_date`, `customer_state`, `total_orders`, `gmv`, `aov`, `avg_review_score`, `delayed_order_pct`, and optionally `prev_day_*` if you added LAGs in Snowflake.

Use the **calculated fields in § “`DS_EXEC_STATE`”** below (same logic as before: `CF_Max_KPI_Date` on `[kpi_date]`, `CF_Date_In_Window`, state filter, Top 10).

**Fallback:** if you ever drop the view, Executive can be rebuilt from **`FCT_ORDERS`** alone (`DS_FCT`, § below).

---

## Existing tables to use

| Tableau data source | Snowflake object | Role |
|---------------------|------------------|------|
| `DS_EXEC_STATE` | `ECOM_ANALYTICS.MART.vw_exec_dashboard` | **Executive (preferred)** — date + state + Top 10 + `p_Last_N_Days` |
| `DS_FCT` | `ECOM_ANALYTICS.MART.FCT_ORDERS` | **Executive (fallback)** — order grain; aggregate in Tableau |
| `DS_DAILY` | `ECOM_ANALYTICS.MART.DAILY_KPI` | **Optional** — fast Brazil-only trend / sanity check (no state) |
| `DS_GEO` | `ECOM_ANALYTICS.MART.GEO_KPI` | **Operations** — map / city / state bars (not daily; all-time in mart) |
| `DS_BRIEF` | `ECOM_ANALYTICS.MART.CLIENT_DAILY_BRIEF` | **Executive** — latest brief |
| `DS_FORECAST` | `ECOM_ANALYTICS.MART.DEMAND_FORECAST_DAILY` | **Forecast & ML** |
| `DS_RISK` | `ECOM_ANALYTICS.MART.DELAY_RISK_SCORES` | **Operations** — heatmap |
| `DS_REGISTRY` | `ECOM_ANALYTICS.MART.MODEL_REGISTRY` | **Forecast & ML** |
| `DS_DRIFT` | `ECOM_ANALYTICS.MART.DRIFT_AUDIT` | **Forecast & ML** |

**Date column on `FCT_ORDERS`:** use **`order_date`** (present in your mart definitions). If your deployed mart differs, use the day field your `DAILY_KPI` is built from — it must match for consistent “last N days.”

---

## Parameters (workbook)

| Name | Type | Default |
|------|------|---------|
| `p_Last_N_Days` | Integer | `60` |
| `p_Min_City_Orders` | Integer | `50` |
| `p_Forecast_State` | String | `SP` |

---

## Calculated fields — `DS_EXEC_STATE` (`vw_exec_dashboard`) — Executive when you use the view

Use **only** fields from this data source (no blending).

```tableau
CF_Max_KPI_Date = { FIXED : MAX([kpi_date]) }

CF_Date_In_Window =
[kpi_date] >= DATEADD('day', -[p_Last_N_Days], [CF_Max_KPI_Date])

CF_Is_Latest_Day = [kpi_date] = [CF_Max_KPI_Date]

CF_Data_As_Of_Text = "Data as of: " + STR([CF_Max_KPI_Date])

// Latest day delay % → show 0 if null
CF_Delayed_Pct_Display =
IF [kpi_date] = [CF_Max_KPI_Date] THEN ZN([delayed_order_pct]) END
```

**DoD (only if your view has `prev_day_gmv`, etc.):**

```tableau
CF_GMV_DoD_Pct =
IF [kpi_date] <> [CF_Max_KPI_Date] THEN NULL
ELSEIF ISNULL([prev_day_gmv]) OR [prev_day_gmv] = 0 THEN NULL
ELSE ([gmv] - [prev_day_gmv]) / [prev_day_gmv]
END
```

Mirror the same pattern for `prev_day_orders`, `prev_day_aov`, `prev_day_delayed_pct`, `prev_day_avg_review_score`. If those columns **do not** exist in your view yet, use **Quick table calculation → Percent difference** on the sparkline only, or skip DoD text.

---

## Calculated fields — `DS_FCT` (`FCT_ORDERS`) — Executive + anything needing date ∩ state

```tableau
// Window end date (latest order day in extract)
CF_Max_Order_Date = { FIXED : MAX([order_date]) }

// Last N days through latest date
CF_Date_In_Window =
[order_date] >= DATEADD('day', -[p_Last_N_Days], [CF_Max_Order_Date])

// Latest day only (for KPI headline numbers)
CF_Is_Latest_Day = [order_date] = [CF_Max_Order_Date]

// Row-level helpers (order grain)
CF_Is_Delayed_01 = INT([is_delayed])

// For KPI text when delay unknown on latest day → 0 %
CF_Delayed_Pct_Latest_Display =
IF [order_date] = [CF_Max_Order_Date] THEN
    ZN(AVG([CF_Is_Delayed_01]) * 100)
END
```

Build KPI **values** on latest day with filters `CF_Date_In_Window` + `customer_state` + `CF_Is_Latest_Day`:

- **GMV:** `SUM([payment_total])`
- **Orders:** `COUNTD([order_id])`
- **AOV:** `SUM([payment_total]) / COUNTD([order_id])`
- **Avg review:** `AVG([avg_review_score])`
- **Delay %:** `AVG([CF_Is_Delayed_01]) * 100` or use `CF_Delayed_Pct_Latest_Display` on a sheet aggregated to headline level

**DoD % without new SQL:** on a **sparkline sheet** (day on Columns, `SUM(payment_total)` on Rows), add a **Quick table calculation → Percent difference** along `order_date`, partitioned by `customer_state`. For a **single headline DoD** when multiple states are selected, use a aggregated sheet: compare `SUM(IF CF_Is_Latest_Day THEN payment_total END)` vs prior day via **LOD** (verbose) or a **second tiny sheet** from `DS_DAILY` national only (below).

---

## Calculated fields — `DS_DAILY` (`DAILY_KPI`) — optional national strip

```tableau
CF_Max_KPI_Date = { FIXED : MAX([kpi_date]) }
CF_Date_In_Window = [kpi_date] >= DATEADD('day', -[p_Last_N_Days], [CF_Max_KPI_Date])
```

Use for a **Brazil-only** line that matches `DAILY_KPI` exactly. Do **not** blend into `DS_FCT` sheets (avoids LOD errors).

---

## Calculated fields — `DS_BRIEF`

```tableau
CF_Max_Brief_Date = { FIXED : MAX([brief_date]) }
CF_Is_Latest_Brief = [brief_date] = [CF_Max_Brief_Date]
```

---

## Calculated fields — `DS_GEO`

```tableau
CF_City_Meets_Volume = SUM([total_orders]) >= [p_Min_City_Orders]
```

---

## Calculated fields — `DS_RISK`

```tableau
CF_Risk_Bucket =
IF [delay_risk_score] >= 0.8 THEN "Very High"
ELSEIF [delay_risk_score] >= 0.6 THEN "High"
ELSEIF [delay_risk_score] >= 0.3 THEN "Medium"
ELSE "Low"
END
```

---

## Calculated fields — `DS_FORECAST` / `DS_REGISTRY` / `DS_DRIFT`

```tableau
// DS_FORECAST
CF_Forecast_State_Match = [customer_state] = [p_Forecast_State]

// DS_REGISTRY
CF_Is_Forecast_Champion = [model_type] = "forecast" AND [is_champion] = TRUE
CF_Is_Delay_Champion = [model_type] = "delay_risk" AND [is_champion] = TRUE

// DS_DRIFT
CF_Is_Drift_Flagged = [drift_flag] = TRUE
CF_Drift_Count = COUNTD(IIF([drift_flag], [feature_name], NULL))
```

---

## Three dashboards (fixed **1600 × 900**)

### 1) Executive

- **Data (preferred):** `DS_EXEC_STATE` (`vw_exec_dashboard`) for KPI row, sparklines, trend, **Top 10 states** — filter `CF_Date_In_Window`, dashboard **State** filter, Top 10 by `SUM([gmv])`.
- **Data (fallback):** `DS_FCT` (`FCT_ORDERS`) with the same filters on `order_date` / `customer_state` if you do not use the view.
- **Brief:** `DS_BRIEF`, filter `CF_Is_Latest_Brief`.
- **Filters:** dashboard `p_Last_N_Days` (via `CF_Date_In_Window` on all `DS_FCT` sheets) + **State** multi-filter on `customer_state`.
- **Optional:** small `DS_DAILY` line for “Brazil (DAILY_KPI)” comparison.

### 2) Operations

- **Map / ranks / cities:** `DS_GEO` (does not follow `p_Last_N_Days` unless you later change the mart).
- **Heatmap:** `DS_RISK` — `customer_state` × `payment_type`, color `AVG(delay_risk_score)`.

### 3) Forecast & ML

- **Actual:** `DS_DAILY` — `kpi_date` vs `total_orders` (national).
- **Forecast:** `DS_FORECAST` + `CF_Forecast_State_Match` + parameter control.
- **Chips / tables:** `DS_REGISTRY`, `DS_DRIFT`.

---

## Extracts & pipeline

Use **Extract** on each data source. After Snowflake tasks run, refresh via `python pipeline/run_tableau_refresh.py` and your Tableau Cloud IDs in `.env`.

---

## Why `vw_exec_dashboard` vs `FCT_ORDERS`

`DAILY_KPI` has no **state**. `GEO_KPI` has no **day**. **`vw_exec_dashboard`** (if you maintain it in Snowflake) is the best Executive source: **state × day**, small extract, one place for filters. **`FCT_ORDERS`** is the no-view fallback at order grain.
