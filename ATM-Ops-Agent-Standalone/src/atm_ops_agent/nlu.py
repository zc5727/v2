from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ParsedIntent:
    action: str
    service: str | None = None
    target: str | None = None


SERVICE_KEYWORDS = (
    "nginx",
    "docker",
    "redis",
    "mysql",
    "mariadb",
    "php-fpm",
    "sshd",
    "nacos",
    "atm-gateway",
    "gateway",
    "atm-service",
    "atm-system",
    "atm-auth",
    "atm-openapi",
    "xxl-job",
    "dolphinscheduler",
    "clickhouse",
    "spark",
    "hadoop",
)


def _extract_service(text: str) -> str | None:
    lowered = text.lower()
    for keyword in SERVICE_KEYWORDS:
        if keyword in lowered:
            return keyword
    return None


def _looks_like_clickhouse_check(text: str) -> bool:
    lowered = text.lower()
    return "clickhouse" in lowered and any(token in lowered for token in ("核验", "校验", "检查", "check", "verify"))


def _looks_like_frontend_rollback_preview(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("前端回滚预检", "回滚预检", "rollback preview", "回滚演练", "前端回滚演练"))


def parse_prompt(text: str) -> ParsedIntent:
    normalized = text.strip().lower()

    if any(word in normalized for word in ("服务器恢复", "恢复预案", "恢复清单", "spark恢复", "web恢复", "恢复步骤")):
        return ParsedIntent(action="server-recovery-plan")

    if _looks_like_clickhouse_check(normalized):
        return ParsedIntent(action="clickhouse-table-check", service="clickhouse")

    if any(word in normalized for word in ("mysql修复", "mysql repair", "mysql 修复", "sql修复", "sql 修复")):
        return ParsedIntent(action="mysql-repair", service="mysql")

    if any(word in normalized for word in ("spark补数", "spark 补数", "spark backfill", "spark 重算", "重跑spark", "重跑 spark")):
        return ParsedIntent(action="spark-backfill", service="spark")

    if any(
        word in normalized
        for word in ("xxl-job重跑", "xxl-job 重跑", "xxl-job重试", "xxl-job 重试", "重试 xxl-job", "重跑 xxl-job", "xxl-job rerun", "xxl 重跑", "调度重跑")
    ):
        return ParsedIntent(action="xxl-job-rerun", service="xxl-job")

    if any(word in normalized for word in ("数据修复", "补数", "回填", "修数", "repair data", "backfill")):
        return ParsedIntent(action="data-repair-plan")

    if _looks_like_frontend_rollback_preview(normalized):
        return ParsedIntent(action="frontend-rollback-preview", service="nginx")

    if any(word in normalized for word in ("回滚", "rollback")):
        return ParsedIntent(action="frontend-rollback", service="nginx")

    if any(word in normalized for word in ("日志分析", "分析日志", "journal", "journalctl")):
        return ParsedIntent(action="log-analysis", service=_extract_service(normalized))

    if any(word in normalized for word in ("docker日志", "容器日志", "docker logs")):
        return ParsedIntent(action="docker-log-analysis", service=_extract_service(normalized))

    if any(word in normalized for word in ("经验库", "相似案例", "历史案例", "知识库")):
        return ParsedIntent(action="knowledge-search")

    if any(word in normalized for word in ("重启", "重载", "restart", "reload")):
        return ParsedIntent(action="service-restart", service=_extract_service(normalized))

    if any(word in normalized for word in ("状态", "服务", "日志", "查看", "status", "log")):
        service = _extract_service(normalized)
        if service:
            return ParsedIntent(action="service-status", service=service)

    if any(word in normalized for word in ("安全", "审计", "登录", "audit")):
        return ParsedIntent(action="security-audit")

    if any(word in normalized for word in ("磁盘", "硬盘", "inode", "disk")):
        return ParsedIntent(action="disk-usage")

    if any(word in normalized for word in ("进程", "cpu", "内存", "memory", "top")):
        return ParsedIntent(action="process-top")

    return ParsedIntent(action="health-check")
