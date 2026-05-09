from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from atm_ops_agent.chat_agent import ChatAgentResult, ChatMessage, OpsChatAgent
from atm_ops_agent.planner import PlannedStep


class ChatGraphState(TypedDict, total=False):
    history: list[ChatMessage]
    prompt: str
    target: str | None
    confirm_change: bool
    approval_ticket: str | None
    operator: str | None
    report: bool
    decision: ChatAgentResult | None
    plan_text: str
    execution_text: str
    final_text: str
    steps: list[PlannedStep]


class OpsChatGraph:
    def __init__(self, chat_agent: OpsChatAgent, *, plan_renderer, execution_renderer) -> None:
        self.chat_agent = chat_agent
        self.plan_renderer = plan_renderer
        self.execution_renderer = execution_renderer
        self.app = self._build_graph().compile()

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(ChatGraphState)
        graph.add_node("decide", self._decide_node)
        graph.add_node("plan", self._plan_node)
        graph.add_node("execute", self._execute_node)
        graph.add_node("compose", self._compose_node)

        graph.add_edge(START, "decide")
        graph.add_conditional_edges(
            "decide",
            self._route_after_decide,
            {
                "reply": "compose",
                "plan": "plan",
                "execute": "plan",
            },
        )
        graph.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {
                "compose": "compose",
                "execute": "execute",
            },
        )
        graph.add_edge("execute", "compose")
        graph.add_edge("compose", END)
        return graph

    def run(
        self,
        *,
        history: list[ChatMessage],
        prompt: str,
        target: str | None,
        confirm_change: bool,
        approval_ticket: str | None,
        operator: str | None,
        report: bool,
    ) -> str:
        result = self.app.invoke(
            {
                "history": history,
                "prompt": prompt,
                "target": target,
                "confirm_change": confirm_change,
                "approval_ticket": approval_ticket,
                "operator": operator,
                "report": report,
            }
        )
        return str(result.get("final_text", "没有生成可执行内容。"))

    def _decide_node(self, state: ChatGraphState) -> ChatGraphState:
        decision = self.chat_agent.decide(
            history=state.get("history", []),
            prompt=state.get("prompt", ""),
            target=state.get("target"),
            confirm_change=bool(state.get("confirm_change", False)),
        )
        return {
            "decision": decision,
            "steps": list(decision.steps),
        }

    def _route_after_decide(self, state: ChatGraphState) -> str:
        decision = state.get("decision")
        if not decision:
            return "reply"
        mode = decision.mode.strip().lower()
        if mode == "execute" and state.get("steps"):
            return "execute"
        if mode == "plan" and state.get("steps"):
            return "plan"
        return "reply"

    def _plan_node(self, state: ChatGraphState) -> ChatGraphState:
        steps = state.get("steps", [])
        if not steps:
            return {"plan_text": ""}
        return {"plan_text": self.plan_renderer(steps)}

    def _route_after_plan(self, state: ChatGraphState) -> str:
        decision = state.get("decision")
        if decision and decision.mode.strip().lower() == "execute" and state.get("steps"):
            return "execute"
        return "compose"

    def _execute_node(self, state: ChatGraphState) -> ChatGraphState:
        steps = state.get("steps", [])
        if not steps:
            return {"execution_text": ""}
        try:
            execution_text = self.execution_renderer(
                prompt=state.get("prompt", ""),
                steps=steps,
                confirm_change=bool(state.get("confirm_change", False)),
                approval_ticket=state.get("approval_ticket"),
                operator=state.get("operator"),
                report=bool(state.get("report", False)),
            )
        except PermissionError as exc:
            execution_text = self._render_approval_required(steps, str(exc))
        return {"execution_text": execution_text}

    def _compose_node(self, state: ChatGraphState) -> ChatGraphState:
        blocks: list[str] = []
        decision = state.get("decision")
        if decision and decision.reply:
            blocks.append(decision.reply)
        if state.get("plan_text"):
            blocks.append("建议动作计划：\n" + state["plan_text"])
        if state.get("execution_text"):
            blocks.append(state["execution_text"])
        if not blocks:
            blocks.append("没有生成可执行内容。")
        return {"final_text": "\n\n".join(blocks)}

    @staticmethod
    def _render_approval_required(steps: list[PlannedStep], reason: str) -> str:
        lines = [
            "已暂停执行：本次命中了变更或高风险动作，平台不会绕过审批直接执行。",
            f"拦截原因：{reason}",
            "",
            "待审批动作：",
        ]
        for index, step in enumerate(steps, start=1):
            lines.append(
                f"{index}. {step.action} / 目标={step.target} / 服务={step.service or '-'} / 原因={step.reason or '-'}"
            )
        lines.extend(
            [
                "",
                "下一步：请填写审批单号，确认这是允许执行的变更后再提交；未审批前我只会提供方案和预览，不会执行数据库修复、重启、回滚或发布动作。",
            ]
        )
        return "\n".join(lines)
