# ATM Ops Agent Knowledge Base

## Purpose

This document is the local master knowledge base for the ATM operations agent.

It consolidates:

- verified environment facts
- ops working rules
- incident handling habits
- command corrections
- data repair principles
- Feishu knowledge-sync rules

Use this file as the starting point before continuing future ATM operations work.

## Current Role

The agent should operate as:

- first-line ATM operations agent
- environment-aware troubleshooting assistant
- change-risk controlled execution assistant
- knowledge archiver for later SOP reuse

## Verified Environment

### Servers

- `atm-app-server`
  - public IP: `8.161.226.223`
  - private IP: `172.20.71.211`
  - role: app / nacos / gateway / nginx / ui / backend services
- `atm-bigdata-server`
  - public IP: `8.161.227.173`
  - private IP: `172.20.71.210`
  - role: xxl-job / dolphinscheduler / clickhouse / spark / hadoop
- `atm-mysql-server`
  - public IP: `8.130.65.254`
  - role: MySQL server

### Key Services

- App side:
  - `nacos` on `8848`
  - `atm-gateway-pre` on `9300`
  - `atm-auth-pre` on `9200`
  - `atm-system-pre` on `9201`
  - `atm-service-pre` on `9202`
  - `atm-openapi-pre` on `9203`
  - nginx / ui on `9400`
- Bigdata side:
  - `atm-job` on `7000`
  - `clickhouse-server` on `8123` and `9000`
  - `hadoop-namenode-pre` on `8020` and `9870`
  - `hadoop-datanode-pre` on `9864`
  - `spark-master-pre` on `7077` and web UI
  - `spark-worker-1-pre` on worker web UI
- Database side:
  - MySQL on `8.130.65.254:3306`

## Agent Working Rules

### General

- prefer read-only inspection before any repair
- prefer verified environment commands over generic assumptions
- treat restart, deployment, rollback, data repair, and replay as high-risk actions
- always preserve evidence before proposing a mutating fix

### Data Repair

- first verify whether source-layer data exists
- then verify scheduler / replay / recompute path
- prefer rerun, replay, or backfill over manual patching
- do not manually patch ADS/DWS result tables unless no safe replay path exists

### Knowledge Archiving

- every meaningful ops action should be summarizable into reusable knowledge
- preserve commands, outputs, findings, root cause, repair action, and verification result
- if command guidance changes after verification, record the correction into command memory

## Verified Command Memory

### Missing-data checks

- do not start from MySQL `atm_job` when the goal is business data gap verification
- first check ClickHouse business tables in `atm_chengdu`

### ClickHouse access

- use authenticated local execution
- verified pattern:
  - `docker exec clickhouse-server clickhouse-client --user default --password '<password>' --query "<SQL>"`

### Result-table repair

- do not repair by directly inserting into ADS / DWS tables first
- verify ODS / DWD / scheduler chain before any write action

## Verified Data Findings From 2026-05-07

### Three-server inspection

- `atm-app-server`
  - external checks were healthy
  - `8848` returned `200`
  - `9400` returned `200`
  - `/prod-api/system/user/getInfo` returned `200`
- `atm-bigdata-server`
  - SSH reachable
  - `ClickHouse` ping returned `Ok.`
  - several bigdata-facing endpoints returned `502`
- `atm-mysql-server`
  - SSH reachable
  - `3306` reachable

### Business data gap investigation

- recent 5-day business data check confirmed missing data
- ClickHouse current server time was verified as `2026-05-07`
- multiple business tables had no rows in the last 5 days
- latest business data in several key tables was stuck around `2025-12-08`

### Important conclusion

- this is not a one-day isolated data gap
- it behaves like an upstream or whole-chain data stoppage
- safe repair must begin with source / scheduler / backfill path verification

## Built-in ATM Skills

### ClickHouse daily verification

- builtin action: `clickhouse-table-check`
- purpose: verify a specified ClickHouse table across the last N days
- required params:
  - `table`
  - `time_column`
- optional params:
  - `database` default `atm_chengdu`
  - `days` default `5`
- output shape:
  - daily row counts
  - overall row count
  - min / max timestamp
- safety:
  - read-only
  - authenticated local `clickhouse-client`

### Frontend rollback preview

- builtin action: `frontend-rollback-preview`
- purpose: preview ATM frontend rollback candidates without changing the current symlink
- optional params:
  - `release_name`
  - `base_dir` default `/data/atm-ui-pre`
- output shape:
  - current symlink target
  - latest release directories
  - selected rollback target
  - `nginx -t` result
  - preview rollback command
- safety:
  - read-only
  - no `ln -sfn`
  - no `systemctl reload nginx`

## Feishu Knowledge Strategy

### Current Direction

- local ops knowledge is the primary source of truth
- Feishu should become the external human-readable knowledge sink

### Current Capability

- can import Feishu docs into local knowledge
- can write back to Feishu docx through the current adapter
- user-visible document creation should prefer `user_access_token` mode

### Pending Follow-up

- finish user authorization flow to switch write-back from app space to user-visible document space
- after user token is ready, use user-token mode as the default sync path

## Recommended Next Session Starting Point

When operations continue next time:

1. start from this knowledge base
2. resume unfinished Feishu user authorization
3. continue bigdata-side root cause tracing for the data pipeline stoppage
4. determine whether the correct repair path is:
   - upstream replay
   - XXL-Job rerun
   - Spark backfill
   - scheduler recovery

## Related Knowledge Files

- `src/knowledge/atm-ops-runbook.md`
- `src/knowledge/atm-command-alignment.md`
- `src/knowledge/atm-feishu-knowledge-sync.md`
