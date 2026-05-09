from __future__ import annotations

import json

from atm_ops_agent.config import LLMConfig
from atm_ops_agent.llm_gateway import chat_completion, llm_ready
from atm_ops_agent.summarizer import ExecutionSummary


def explain_execution(
    config: LLMConfig,
    *,
    prompt: str,
    server_name: str,
    action: str,
    summary: ExecutionSummary,
    stdout: str,
    stderr: str,
) -> str:
    fallback = _fallback_explanation(prompt=prompt, server_name=server_name, action=action, summary=summary)
    if not llm_ready(config):
        return fallback
    try:
        return _llm_explain(
            config,
            prompt=prompt,
            server_name=server_name,
            action=action,
            summary=summary,
            stdout=stdout,
            stderr=stderr,
        )
    except Exception:
        return fallback


def _llm_explain(
    config: LLMConfig,
    *,
    prompt: str,
    server_name: str,
    action: str,
    summary: ExecutionSummary,
    stdout: str,
    stderr: str,
) -> str:
    system_prompt = (
        "你是资深运维助手。请根据用户原始需求、执行动作、结构化总结和日志片段，"
        "用简洁自然的中文输出一段面向用户的结果说明。"
        "必须包含：1. 动作是否成功 2. 问题是否已解决 3. 当前最值得关注的异常 4. 下一步建议。"
        "不要输出 JSON。"
    )
    user_prompt = json.dumps(
        {
            "user_request": prompt,
            "server": server_name,
            "action": action,
            "summary": {
                "action_success": summary.action_success,
                "issue_resolved": summary.issue_resolved,
                "headline": summary.headline,
                "findings": summary.findings,
            },
            "stdout_excerpt": (stdout or "")[:3000],
            "stderr_excerpt": (stderr or "")[:1200],
        },
        ensure_ascii=False,
    )
    return chat_completion(
        config,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        json_mode=False,
    )


def _fallback_explanation(*, prompt: str, server_name: str, action: str, summary: ExecutionSummary) -> str:
    findings = "；".join(summary.findings[:3]) if summary.findings else "未发现明显异常信号"
    return (
        f"关于“{prompt}”，我已经在 {server_name} 上执行了 {action}。\n"
        f"这次动作{'成功' if summary.action_success else '失败'}，当前判断问题是否解决：{summary.issue_resolved}。\n"
        f"当前结论是：{summary.headline}。\n"
        f"最值得关注的信息：{findings}。\n"
        "如果你愿意，我可以继续往下做更细的定位或给出下一步处理建议。"
    )
