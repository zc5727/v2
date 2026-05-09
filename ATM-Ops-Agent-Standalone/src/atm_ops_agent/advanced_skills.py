from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SkillSpec:
    name: str
    mutating: bool
    description: str


ADVANCED_SKILLS: dict[str, SkillSpec] = {
    "log-analysis": SkillSpec("log-analysis", False, "Analyze systemd service logs"),
    "docker-log-analysis": SkillSpec("docker-log-analysis", False, "Analyze Docker container logs"),
    "app-log-search": SkillSpec("app-log-search", False, "Search application logs by keyword"),
    "clickhouse-table-check": SkillSpec("clickhouse-table-check", False, "Validate one ClickHouse table across the last N days"),
    "knowledge-search": SkillSpec("knowledge-search", False, "Search historical incidents and operations knowledge"),
    "data-repair-plan": SkillSpec("data-repair-plan", False, "Generate a data-repair plan and validation steps"),
    "data-repair-run": SkillSpec("data-repair-run", True, "Run an approved generic data-repair command"),
    "mysql-repair": SkillSpec("mysql-repair", True, "Execute MySQL repair SQL and verify the result"),
    "spark-backfill": SkillSpec("spark-backfill", True, "Trigger Spark backfill or recompute jobs"),
    "xxl-job-rerun": SkillSpec("xxl-job-rerun", True, "Retry or trigger an XXL-Job task"),
    "frontend-rollback": SkillSpec("frontend-rollback", True, "Rollback ATM frontend to the previous or specified release"),
    "frontend-rollback-preview": SkillSpec("frontend-rollback-preview", False, "Preview ATM frontend rollback candidates and commands"),
    "server-recovery-plan": SkillSpec("server-recovery-plan", False, "Output a server recovery checklist"),
}


def all_action_names(base_actions: list[str], extra_actions: list[str] | None = None) -> list[str]:
    merged = set(base_actions) | set(ADVANCED_SKILLS.keys())
    if extra_actions:
        merged.update(extra_actions)
    return sorted(merged)


def build_log_analysis_command(service: str | None, params: Mapping[str, str]) -> str:
    lines = _int_param(params, "lines", 200)
    keyword = params.get("keyword", "").strip()
    if service:
        cmd = f"set -e; echo '### SERVICE'; systemctl status {shlex.quote(service)} --no-pager || true; "
        cmd += f"echo '### JOURNAL'; journalctl -u {shlex.quote(service)} -n {lines} --no-pager"
    else:
        cmd = f"set -e; journalctl -n {lines} --no-pager"
    if keyword:
        cmd += f" | grep -i {shlex.quote(keyword)} || true"
    return cmd


def build_docker_log_command(service: str | None, params: Mapping[str, str]) -> str:
    container = params.get("container") or service
    if not container:
        raise ValueError("docker-log-analysis missing service or params.container")
    lines = _int_param(params, "lines", 200)
    return (
        "set -e; "
        "docker ps --format 'table {{.Names}}\\t{{.Status}}'; "
        "echo '### LOGS'; "
        f"docker logs --tail {lines} {shlex.quote(container)} 2>&1"
    )


def build_app_log_search_command(params: Mapping[str, str]) -> str:
    path = params.get("path", "").strip()
    keyword = params.get("keyword", "").strip()
    lines = _int_param(params, "lines", 200)
    if not path or not keyword:
        raise ValueError("app-log-search missing params.path or params.keyword")
    return (
        "set -e; "
        f"echo '### SEARCH_PATH'; echo {shlex.quote(path)}; "
        f"echo '### KEYWORD'; echo {shlex.quote(keyword)}; "
        f"grep -RIn --binary-files=without-match --color=never {shlex.quote(keyword)} {shlex.quote(path)} | tail -n {lines}"
    )


def build_clickhouse_table_check_command(params: Mapping[str, str]) -> str:
    database = params.get("database", "atm_chengdu").strip()
    table = params.get("table", "").strip()
    time_column = params.get("time_column", "").strip()
    days = _int_param(params, "days", 5)
    if not table or not time_column:
        raise ValueError("clickhouse-table-check missing params.table and params.time_column")

    query_daily = (
        f"SELECT toDate({time_column}) AS day, count() AS rows "
        f"FROM {database}.{table} "
        f"WHERE {time_column} >= subtractDays(today(), {days}) AND {time_column} < today() "
        "GROUP BY day ORDER BY day FORMAT TabSeparated"
    )
    query_minmax = (
        f"SELECT count() AS total_rows, min({time_column}) AS min_ts, max({time_column}) AS max_ts "
        f"FROM {database}.{table} FORMAT TabSeparated"
    )
    password_expr = "$(sed -n 's:.*<password>\\(.*\\)</password>.*:\\1:p' /data/clickhouse/config/users.xml 2>/dev/null | head -1)"
    return (
        "set -e; "
        f"CLICKHOUSE_PASSWORD={password_expr}; "
        "test -n \"$CLICKHOUSE_PASSWORD\"; "
        f"echo '### TABLE'; echo {shlex.quote(database + '.' + table)}; "
        f"echo '### TIME_COLUMN'; echo {shlex.quote(time_column)}; "
        f"echo '### WINDOW_DAYS'; echo {days}; "
        "echo '### DAILY'; "
        f"docker exec clickhouse-server clickhouse-client --user default --password \"$CLICKHOUSE_PASSWORD\" --query {shlex.quote(query_daily)}; "
        "echo '### MIN_MAX'; "
        f"docker exec clickhouse-server clickhouse-client --user default --password \"$CLICKHOUSE_PASSWORD\" --query {shlex.quote(query_minmax)}"
    )


def build_frontend_rollback_command(params: Mapping[str, str]) -> str:
    release_name = params.get("release_name", "").strip()
    base_dir = params.get("base_dir", "/data/atm-ui-pre")
    if release_name:
        target_expr = f"{base_dir}/releases/{shlex.quote(release_name)}/atm-ui/dist"
        validate = f"test -d {target_expr}"
    else:
        target_expr = "$(ls -1dt " + shlex.quote(base_dir) + "/releases/* | sed -n '2p')/atm-ui/dist"
        validate = "test -d \"$target\""
    return (
        "set -e; "
        f"cd {shlex.quote(base_dir)}; "
        f"target={target_expr}; "
        "echo '### RELEASES'; ls -1dt releases/* | head -5; "
        f"{validate}; "
        "ln -sfn \"$target\" current; "
        "nginx -t; systemctl reload nginx; "
        "echo '### CURRENT'; readlink -f current"
    )


def build_frontend_rollback_preview_command(params: Mapping[str, str]) -> str:
    release_name = params.get("release_name", "").strip()
    base_dir = params.get("base_dir", "/data/atm-ui-pre")
    if release_name:
        target_expr = f"{base_dir}/releases/{shlex.quote(release_name)}/atm-ui/dist"
    else:
        target_expr = "$(ls -1dt " + shlex.quote(base_dir) + "/releases/* | sed -n '2p')/atm-ui/dist"
    return (
        "set -e; "
        f"cd {shlex.quote(base_dir)}; "
        "echo '### CURRENT'; readlink -f current || true; "
        "echo '### RELEASES'; ls -1dt releases/* | head -10; "
        f"target={target_expr}; "
        "echo '### TARGET'; echo \"$target\"; "
        "test -d \"$target\"; "
        "echo '### NGINX_TEST'; nginx -t; "
        "echo '### PREVIEW_COMMAND'; "
        "echo \"ln -sfn $target current && systemctl reload nginx\""
    )


def build_data_repair_run_command(params: Mapping[str, str]) -> str:
    command = params.get("command", "").strip()
    validation = params.get("validation_command", "").strip()
    if not command:
        raise ValueError("data-repair-run missing params.command")
    full = f"set -e; {command}"
    if validation:
        full += f"; echo '### VALIDATION'; {validation}"
    return full


def build_mysql_repair_command(params: Mapping[str, str]) -> str:
    mysql_cmd = params.get("mysql_cmd", "mysql")
    database = params.get("database", "").strip()
    sql = params.get("sql", "").strip()
    validation_sql = params.get("validation_sql", "").strip()
    if not database or not sql:
        raise ValueError("mysql-repair missing params.database and params.sql")
    full = f"set -e; {mysql_cmd} -N -s -e {shlex.quote(f'USE {database}; {sql}')}; "
    if validation_sql:
        full += f"echo '### VALIDATION'; {mysql_cmd} -N -s -e {shlex.quote(f'USE {database}; {validation_sql}')}"
    return full


def build_spark_backfill_command(params: Mapping[str, str]) -> str:
    submit = params.get("submit_command", "").strip()
    validation = params.get("validation_command", "").strip()
    if not submit:
        raise ValueError("spark-backfill missing params.submit_command")
    full = f"set -e; {submit}"
    if validation:
        full += f"; echo '### VALIDATION'; {validation}"
    return full


def build_xxl_job_rerun_command(params: Mapping[str, str]) -> str:
    trigger = params.get("trigger_command", "").strip()
    validation = params.get("validation_command", "").strip()
    if not trigger:
        raise ValueError("xxl-job-rerun missing params.trigger_command")
    full = f"set -e; {trigger}"
    if validation:
        full += f"; echo '### VALIDATION'; {validation}"
    return full


def _int_param(params: Mapping[str, str], key: str, default: int) -> int:
    raw = str(params.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(1, min(value, 5000))
