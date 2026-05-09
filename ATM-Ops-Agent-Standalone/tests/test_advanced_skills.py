from atm_ops_agent.advanced_skills import (
    ADVANCED_SKILLS,
    build_clickhouse_table_check_command,
    build_frontend_rollback_preview_command,
)
from atm_ops_agent.nlu import parse_prompt


def test_clickhouse_table_check_skill_is_registered_and_readonly():
    skill = ADVANCED_SKILLS["clickhouse-table-check"]

    assert skill.mutating is False

    command = build_clickhouse_table_check_command(
        {
            "database": "atm_chengdu",
            "table": "dwd_traffic_event",
            "time_column": "event_time",
            "days": "7",
        }
    )

    assert "docker exec clickhouse-server clickhouse-client" in command
    assert "FROM atm_chengdu.dwd_traffic_event" in command
    assert "subtractDays(today(), 7)" in command
    assert "### DAILY" in command
    assert "### MIN_MAX" in command


def test_frontend_rollback_preview_skill_is_registered_and_non_mutating():
    skill = ADVANCED_SKILLS["frontend-rollback-preview"]

    assert skill.mutating is False

    command = build_frontend_rollback_preview_command({"release_name": "release-20260507"})

    assert "readlink -f current" in command
    assert "ls -1dt releases/* | head -10" in command
    assert "nginx -t" in command
    assert "### PREVIEW_COMMAND" in command
    assert 'echo "ln -sfn $target current && systemctl reload nginx"' in command
    assert 'ln -sfn "$target" current;' not in command


def test_parse_prompt_detects_new_skill_intents():
    clickhouse_intent = parse_prompt("帮我做一个 ClickHouse 指定表近7天核验")
    rollback_intent = parse_prompt("先做一次 ATM 前端回滚预检")

    assert clickhouse_intent.action == "clickhouse-table-check"
    assert clickhouse_intent.service == "clickhouse"
    assert rollback_intent.action == "frontend-rollback-preview"
    assert rollback_intent.service == "nginx"
