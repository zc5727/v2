# ATM Command Alignment Memory

## Purpose

This file records cases where the suggested ops command differs from the command path proven by the real ATM environment.

Use it as local learning memory for the ATM ops agent:

- prefer environment-verified commands over generic assumptions
- preserve the original command context
- record the corrected command or corrected execution path
- explain why the correction is necessary

## How To Use

When a command suggestion is later found to be inaccurate or incomplete:

1. Record the original suggestion.
2. Record the verified command or verified target system.
3. Record the reason for the delta.
4. Reuse the corrected version in future operations.

## Alignment Records

### 2026-05-07 - Data gap checks must target ClickHouse business tables first

- Context: checking whether recent business data is missing.
- Original assumption: start from MySQL database `atm_job`.
- Corrected target: ClickHouse database `atm_chengdu`.
- Verified tables:
  - `atm_chengdu.dwd_traffic_event`
  - `atm_chengdu.dws_vehicle_data_cross_section_summary_all`
  - `atm_chengdu.dws_standard_section_analyse_result`
  - `atm_chengdu.ads_section_60_minutes_data`
- Reason:
  - `atm_job` is the scheduler/admin metadata database used by XXL-Job.
  - Business result data is stored in ClickHouse `atm_chengdu`.
  - Missing-data checks for recent production-style metrics must verify source/result tables in ClickHouse before considering scheduler metadata repair.

### 2026-05-07 - ClickHouse requires explicit authenticated local execution

- Context: querying business tables on `atm-bigdata-server`.
- Original assumption: `clickhouse-client` default user without explicit password may work.
- Corrected command path:
  - `docker exec clickhouse-server clickhouse-client --user default --password '<verified-password>' --query "<SQL>"`
- Reason:
  - The server is configured with an explicit password for `default`.
  - Unauthenticated local calls fail with `AUTHENTICATION_FAILED`.

### 2026-05-07 - Data repair must not start with direct result-table patching

- Context: user asks to check and fix recent missing data.
- Original risky shortcut: repair result tables directly once a gap is observed.
- Corrected rule:
  - first verify whether source-layer data exists
  - then verify scheduler/backfill path
  - prefer replay, rerun, or recompute
  - avoid manual inserts into ADS/DWS result tables unless no safe replay path exists
- Reason:
  - This environment follows source-task-result consistency rules.
  - Direct manual patching can create irreversible inconsistency between ODS, DWD, DWS, and ADS layers.
