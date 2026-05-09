from __future__ import annotations

from datetime import datetime
from pathlib import Path

from atm_ops_agent.config import Server
from atm_ops_agent.ssh_client import CommandResult
from atm_ops_agent.summarizer import summarize_execution


def write_report(
    report_dir: str,
    server: Server,
    action: str,
    result: CommandResult,
    *,
    incident_id: str | None = None,
    prompt: str | None = None,
    reason: str | None = None,
    service: str | None = None,
) -> Path:
    output_dir = Path(report_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"{server.name}-{action}-{timestamp}.md"
    summary = summarize_execution(action, result)
    content = "\n".join(
        [
            f"# 运维报告: {action}",
            "",
            f"- 服务器: `{server.name}`",
            f"- 主机: `{server.host}`",
            f"- 区域: `{server.region or 'N/A'}`",
            f"- 实例 ID: `{server.instance_id or 'N/A'}`",
            f"- 事故单: `{incident_id or 'N/A'}`",
            f"- 服务名: `{service or 'N/A'}`",
            f"- 时间: `{datetime.now().isoformat(timespec='seconds')}`",
            f"- 返回码: `{result.returncode}`",
            f"- 原始需求: {prompt or 'N/A'}",
            f"- 执行原因: {reason or 'N/A'}",
            "",
            "## 结论",
            "",
            f"- 动作是否成功: `{'是' if summary.action_success else '否'}`",
            f"- 问题是否解决: `{summary.issue_resolved}`",
            f"- 总结: {summary.headline}",
            "",
            "## 发现",
            "",
            *(["- " + item for item in summary.findings] or ["- 未发现明显异常信号"]),
            "",
            "## 执行命令",
            "",
            "```bash",
            result.command,
            "```",
            "",
            "## 标准输出",
            "",
            "```text",
            result.stdout or "(empty)",
            "```",
            "",
            "## 标准错误",
            "",
            "```text",
            result.stderr or "(empty)",
            "```",
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    return path
