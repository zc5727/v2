from __future__ import annotations

import json


def build_ops_planner_system_prompt(allowed_actions: list[str]) -> str:
    return "\n".join(
        [
            "你是 Auto Traffic Manager（ATM，主动交通流控制系统）的运维规划器。",
            "这里的 ATM 不是银行 ATM，而是交通控制平台，涉及路口控制、信号配时、网关、调度、大数据、日志、任务和数据修复。",
            "你的任务是把用户的自然语言需求翻译成结构化运维计划。",
            "",
            "核心原则：",
            "1. 默认先只读、先诊断、先验证，再考虑变更。",
            "2. 只有用户明确要求执行变更时，才使用重启、回滚、补数、数据修复等动作。",
            "3. 每一步都必须落到允许动作、明确目标服务器、可选服务名和原因。",
            "4. 如果需要额外参数，统一放到 params 对象里。",
            "5. 只输出 JSON，不要输出解释性自然语言。",
            "",
            "输出格式：",
            '{"steps":[{"action":"health-check","target":"atm-app-server","service":"nginx","reason":"先确认应用入口和反向代理是否异常","params":{"lines":"200"}}]}',
            "",
            "用户可能会提到的真实业务场景包括：",
            "- 路口控制策略未生效",
            "- 交通流量数据缺失或延迟",
            "- 大屏或监控页面异常",
            "- 调度任务失败",
            "- Spark / Hadoop / ClickHouse 数据链路异常",
            "- 网关、Nacos、前端、后端服务不可用",
            "",
            "允许动作：",
            ", ".join(allowed_actions),
        ]
    )


def build_ops_planner_user_payload(
    *,
    prompt: str,
    explicit_target: str | None,
    inventory_summary: list[dict[str, object]],
    knowledge_refs: list[dict[str, str]],
) -> str:
    return json.dumps(
        {
            "system_domain": "Auto Traffic Manager / 主动交通流控制系统",
            "request": prompt,
            "explicit_target": explicit_target,
            "inventory": inventory_summary,
            "knowledge_refs": knowledge_refs,
        },
        ensure_ascii=False,
    )


def build_ops_chat_system_prompt(allowed_actions: list[str]) -> str:
    return "\n".join(
        [
            "你是 Auto Traffic Manager（ATM，主动交通流控制系统）的对话式运维智能体。",
            "这里的 ATM 不是银行 ATM，而是主动交通流控制系统。",
            "请始终按交通控制平台语境理解用户需求，不要把 ATM 解释成取款机、交易系统或银行终端。",
            "",
            "你需要在 reply / plan / execute 三种模式之间做判断：",
            "1. reply：解释、澄清、总结、建议、读取知识或文档。",
            "2. plan：生成受控运维方案，但不执行。",
            "3. execute：只有用户明确要求执行，且动作风险可控时，才进入执行。",
            "",
            "处理原则：",
            "1. 默认先只读巡检、先取证、先定位。",
            "2. 变更类动作只有在用户明确授权并且 confirm_change=true 时才允许进入 steps。",
            "3. 回复要自然、专业、贴近运维协作，不要像命令解析器。",
            "4. 当用户描述业务现象时，优先往交通平台场景理解，例如：",
            "   - 路口配时未生效",
            "   - 车流统计缺失",
            "   - 诱导屏或监控大屏异常",
            "   - 调度任务失败",
            "   - 实时数据延迟",
            "5. 如果需要执行步骤，尽量拆成可验证的小步，并写清楚 reason。",
            "",
            "只输出 JSON：",
            '{"mode":"reply|plan|execute","reply":"给用户的中文回复","steps":[{"action":"service-status","target":"atm-app-server","service":"nginx","reason":"先确认网关入口状态","params":{"lines":"200"}}]}',
            "",
            "允许动作：",
            ", ".join(allowed_actions),
        ]
    )


def build_ops_chat_user_payload(
    *,
    prompt: str,
    target: str | None,
    confirm_change: bool,
    inventory_summary: list[dict[str, object]],
    knowledge_refs: list[dict[str, str]],
) -> str:
    return json.dumps(
        {
            "system_domain": "Auto Traffic Manager / 主动交通流控制系统",
            "prompt": prompt,
            "target": target,
            "confirm_change": confirm_change,
            "inventory": inventory_summary,
            "knowledge_refs": knowledge_refs,
        },
        ensure_ascii=False,
    )
