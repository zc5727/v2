# ATM Ops Agent Standalone

Standalone ATM operations agent package for direct deployment on another platform.

## Included

- CLI entrypoint
- HTTP API entrypoint
- built-in ATM knowledge base
- deploy templates
- Feishu import / sync support
- built-in skills including:
  - `clickhouse-table-check`
  - `frontend-rollback-preview`

## Not Included

- original management platform
- original frontend application
- historical runtime data from the source workspace

## Project Layout

```text
src/ops_agent/      agent runtime and API
src/knowledge/      built-in ATM knowledge base
src/templates/      deploy templates
tests/              focused standalone tests
ops_data/           runtime incidents / audits
reports/            generated reports
knowledge/          synced external knowledge snapshots
```

## Quick Start

```bash
pip install -e ".[dev]"
copy inventory.atm.example.toml inventory.atm.toml
```

Then edit `inventory.atm.toml`, set your SSH host values, and export the SSH password env var if needed.

## Common Commands

```bash
atm-ops-agent inventory list --config inventory.atm.toml
atm-ops-agent api serve --config inventory.atm.toml --host 127.0.0.1 --port 8790
atm-ops-agent ask "帮我巡检 atm-bigdata-server" --config inventory.atm.toml
atm-ops-agent run clickhouse-table-check --config inventory.atm.toml --target atm-bigdata-server --params-json "{\"table\":\"dwd_traffic_event\",\"time_column\":\"event_time\",\"days\":\"5\"}"
```

## Notes

- mutating actions still require `--confirm-change`
- Feishu user-visible document sync requires a valid user authorization flow
- runtime writes to `ops_data/`, `reports/`, and `knowledge/feishu-imports/`
