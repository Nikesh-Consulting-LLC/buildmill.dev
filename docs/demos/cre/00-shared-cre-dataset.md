# 00 · Shared synthetic CRE portfolio dataset

Used by [01 report conversion](01-legacy-report-conversion.md), [02 Power BI as code](02-power-bi-as-code.md) and [05 text-to-SQL evals](05-text-to-sql-evals.md). One generator, one seed, one output folder that each of those repos vendors in (copy the CSVs and the `.duckdb` file — do not make the demos depend on each other at run time). Build this first.

## Why one dataset

Three demos telling the same story about the same ten buildings is more convincing than three demos with three unrelated toy schemas. The audience recognizes the shapes — rent roll, GL, T-12, loan covenants — and the numbers agree across the dbt marts, the Power BI measures, and the answers the text-to-SQL agent gives. Reuse also means every planted anomaly (below) is available to all three.

## Generator

`tools/gen_portfolio.py` — Python 3.12, `pandas` + `duckdb` + `numpy`, `random.seed(4109)` / `numpy` `default_rng(4109)`. Deterministic: same seed → byte-identical CSVs, so any repo can regenerate and diff. Writes:

```
data/
├── portfolio.duckdb              # every table below, loaded
├── csv/<table>.csv               # the same tables as CSV (Power BI import; dbt seeds)
└── README.md                     # table list, row counts, the anomalies, the seed
```

Size budget: **≤ 5 MB** total (36 months × ~400 units × monthly charges is ~15k rows; GL is ~30k rows). Well under the 25 MB workspace zip cap even after each demo adds its own files.

## Tables

| Table | Rows (≈) | Key columns | Notes |
|---|---|---|---|
| `properties` | 10 | `property_id, name, asset_class (office/multifamily/industrial/retail), market, submarket, address, sf_total, year_built, acquired_on, purchase_price` | 3 office, 3 multifamily, 2 industrial, 2 retail; markets NYC / Dallas / Denver / Atlanta |
| `buildings` | 14 | `building_id, property_id, name, floors, sf` | some properties have 2 buildings |
| `units` | ~400 | `unit_id, building_id, unit_number, floor, sf, unit_type` | multifamily units are small; office/industrial suites large |
| `tenants` | ~220 | `tenant_id, name, industry, credit_rating, parent_company` | a handful of parents with several subsidiaries (for "top tenants" roll-ups) |
| `leases` | ~330 | `lease_id, unit_id, tenant_id, start_date, end_date, term_months, base_rent_monthly, escalation_pct, escalation_month, recovery_type (NNN/gross/modified), security_deposit, renewal_options (json), status (active/expired/future/mtm)` | history over 36 months + a few futures; **one lease deliberately has `end_date NULL` and status `mtm`** |
| `charges` | ~15k | `charge_id, lease_id, period (YYYY-MM-01), charge_type (base_rent/cam/tax/insurance/parking/other), amount_billed, amount_collected, collected_on` | monthly billing rows; delinquency = billed − collected aged by period |
| `occupancy_snapshots` | 360 | `property_id, as_of (month end), units_total, units_occupied, sf_total, sf_occupied` | derived by the generator, stored so legacy reports can be reconciled against something fixed |
| `gl_accounts` | ~40 | `account_id, account_no, name, type (revenue/opex/capex/debt_service/below_line)` | a small CRE chart: 4xxx revenue, 5xxx opex, 6xxx capex, 7xxx debt service |
| `gl_entries` | ~30k | `entry_id, property_id, account_id, period, amount, source (billing/ap/payroll/manual/accrual)` | revenue rows tie to `charges` per property/period **except one month at one property** (planted) |
| `budgets` | ~5k | `property_id, account_id, fiscal_year, period, amount` | 3 fiscal years; variance to actual is realistic (±3–15%) |
| `loans` | 8 | `loan_id, property_id, lender, original_balance, current_balance, rate_type (fixed/floating), rate_pct, index (SOFR/null), spread_bps, rate_cap_strike, rate_cap_expires_on, maturity_on, amortization_months, io_months, dscr_covenant, ltv_covenant` | two floating loans with rate caps expiring inside the next 12 months; one loan maturing in 9 months |
| `loan_payments` | ~290 | `loan_id, period, interest_paid, principal_paid, balance_after` | monthly, 36 months |
| `valuations` | 20 | `property_id, as_of, value, cap_rate, source (appraisal/internal)` | two per property, for LTV |
| `capital_calls` / `distributions` | ~30 | `fund_id, property_id, period, amount, kind` | just enough for #4's cash flows if you want them warehouse-sourced (its tests use the Excel model, not this) |

Derived views the generator also creates in DuckDB (handy for legacy `expected/` files and for eval cases):

- `v_rent_roll_current` — one row per occupied unit as of the latest month: property, unit, tenant, sf, base rent, rent PSF, lease end.
- `v_t12_by_property` — trailing-12 revenue / opex / NOI by property from `gl_entries`.
- `v_noi_by_property_month` — the monthly series behind the T-12.
- `v_delinquency_aging` — 0–30 / 31–60 / 61–90 / 90+ by tenant.
- `v_lease_expirations` — expirations by quarter with sf and rent rolling off.
- `v_dscr_by_loan` — NOI / debt service, trailing 12, per loan, next to its covenant.
- `v_ltv_by_loan` — current balance / latest valuation, next to its covenant.

## Planted anomalies (write these down; the stories depend on them)

| Anomaly | Where | Which demo finds it |
|---|---|---|
| Unit `12B` at *Riverside Commons* appears twice in the legacy occupancy-trend report source (a UNION without DISTINCT in the SSRS query) — the legacy report over-counts occupied units by one from month 14 on | `legacy/expected/occupancy_trend.csv` in #1 is generated **with** the double count; the DuckDB view is right | #1's bug story: "occupancy trend double-counts unit 12B" |
| One lease with `end_date NULL` and status `mtm` | `leases` | #1's lease-expiration mart must decide how to show it (a documented rule, not a silent drop); #5 has an eval case for "expiring in 18 months" that must not crash |
| Revenue in `gl_entries` for *Northgate Industrial*, month 22, is 1,250.00 less than billed `charges` (a manual journal was posted to the wrong property) | `gl_entries` | #1's reconciliation test for the T-12 mart shows a variance; the story documents it instead of matching to it |
| Two subsidiaries of *Meridian Health* are separate `tenants` rows with a shared `parent_company` | `tenants` | #1's "top tenants" report rolls up by parent; the legacy Excel version did not, so the top-10 order differs |
| A floating loan whose rate cap expires in 4 months | `loans` | #5 eval case; a natural second-meeting "covenant monitor" story |
| Industrial units have `sf` but several have `base_rent_monthly` quoted **annually** in the legacy source | shows only in #1's `legacy/` files | #2's bug "Rent PSF blank for industrial" and #1's rent-roll conversion both meet it |

Keep the list in `data/README.md` so an agent that reads the repo can find them — the point is not to trick the agent, it's that the *story* names the anomaly and the acceptance criterion says what to do with it.

## What "done" looks like

- `python tools/gen_portfolio.py` regenerates `data/` deterministically; a second run produces no diff.
- `duckdb data/portfolio.duckdb "select count(*) from charges"` returns the row count in `data/README.md`.
- Revenue by property/month from `charges` equals revenue from `gl_entries` for every property/month **except** the one planted variance.
- Occupancy from `occupancy_snapshots` equals the count of active leases per property/month from `leases`.
- The DuckDB file and CSVs are committed to each consuming repo (vendored, not fetched) so a code run has them on disk with no network.
