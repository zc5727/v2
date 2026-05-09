from __future__ import annotations

import json
from typing import Iterable
from urllib.request import Request, urlopen

from atm_ops_agent.config import LLMConfig
from atm_ops_agent.ops_store import KnowledgeEntry


def render_data_repair_plan(
    config: LLMConfig,
    *,
    prompt: str,
    target: str | None,
    knowledge_hits: Iterable[KnowledgeEntry],
) -> str:
    fallback = _fallback_repair_plan(prompt=prompt, target=target, knowledge_hits=knowledge_hits)
    if not config.enabled or not config.api_key:
        return fallback
    try:
        return _llm_repair_plan(config, prompt=prompt, target=target, knowledge_hits=knowledge_hits)
    except Exception:
        return fallback


def _llm_repair_plan(
    config: LLMConfig,
    *,
    prompt: str,
    target: str | None,
    knowledge_hits: Iterable[KnowledgeEntry],
) -> str:
    refs = [
        {
            "title": item.title,
            "target": item.target,
            "resolution": item.resolution,
            "headline": item.final_headline,
            "findings": item.key_findings,
        }
        for item in list(knowledge_hits)[:5]
    ]
    system_prompt = (
        "你是资深数据运维修复助手。请根据用户需求和历史相似案例，输出一个专业的中文数据修复方案。"
        "必须包含：1. 修复目标 2. 风险和前置检查 3. 核验 SQL/命令 4. 修复动作建议 5. 修复后验证 6. 回滚预案。"
        "如果信息不足，要明确指出缺少哪些字段。不要输出 JSON。"
    )
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "prompt": prompt,
                        "target": target,
                        "knowledge_hits": refs,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    request = Request(
        config.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    with urlopen(request, timeout=config.timeout_seconds) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return str(body["choices"][0]["message"]["content"]).strip()


def _fallback_repair_plan(
    *,
    prompt: str,
    target: str | None,
    knowledge_hits: Iterable[KnowledgeEntry],
) -> str:
    refs = list(knowledge_hits)[:3]
    lines = [
        "数据修复方案",
        "",
        f"目标服务器: {target or '未指定'}",
        f"修复需求: {prompt}",
        "",
        "1. 修复目标",
        "- 明确修复范围、业务主键、日期范围、受影响表或任务。",
        "",
        "2. 风险与前置检查",
        "- 先确认是否有最近备份或可回滚快照。",
        "- 先执行只读核验 SQL / 校验命令，确认缺失或脏数据真实存在。",
        "- 修复动作优先在预发或影子表验证。",
        "",
        "3. 建议的核验动作",
        "- 统计缺失数量、受影响时间范围、主键范围。",
        "- 对比上游源数据、下游结果表、调度任务执行记录。",
        "- 记录修复前基线，便于修复后比对。",
        "",
        "4. 修复动作建议",
        "- 如果是缺数，优先采用补跑任务或重算，而不是手工逐条修改。",
        "- 如果是脏数据，优先通过临时表/中间结果校验后再覆盖。",
        "- 如果必须执行 SQL 或 shell 修复，先生成可审查脚本，再人工确认执行。",
        "",
        "5. 修复后验证",
        "- 再次执行修复前的核验 SQL / 命令。",
        "- 校验相关接口、报表、调度状态、下游依赖是否恢复。",
        "",
        "6. 回滚预案",
        "- 保留修复前快照、导出文件或临时表。",
        "- 明确失败时如何撤销 SQL、恢复表或重跑旧版本任务。",
    ]
    if refs:
        lines.extend(["", "历史相似案例参考:"])
        for item in refs:
            lines.append(f"- {item.title} / {item.final_headline} / resolution={item.resolution}")
    return "\n".join(lines)
