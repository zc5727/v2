from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.request import Request, urlopen

from atm_ops_agent.config import AlertRule, AlertingConfig, Server
from atm_ops_agent.monitor import MetricSnapshot


@dataclass(slots=True)
class AlertEvent:
    server: str
    metric: str
    value: float
    operator: str
    threshold: float
    severity: str
    description: str

    def render_text(self) -> str:
        return (
            f"[{self.severity}] server={self.server} metric={self.metric} "
            f"value={self.value:.2f} rule={self.operator} {self.threshold:.2f} "
            f"{self.description}".strip()
        )


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == "==":
        return value == threshold
    raise ValueError(f"unsupported operator: {operator}")


class AlertService:
    def __init__(self, config: AlertingConfig) -> None:
        self.config = config

    def evaluate(self, server: Server, snapshot: MetricSnapshot) -> list[AlertEvent]:
        metric_map = snapshot.as_dict()
        events: list[AlertEvent] = []
        for rule in self.config.rules:
            value = metric_map.get(rule.metric)
            if value is None:
                continue
            if _compare(value, rule.operator, rule.threshold):
                events.append(
                    AlertEvent(
                        server=server.name,
                        metric=rule.metric,
                        value=value,
                        operator=rule.operator,
                        threshold=rule.threshold,
                        severity=rule.severity,
                        description=rule.description,
                    )
                )
        return events

    def notify(self, events: list[AlertEvent]) -> None:
        if not events:
            return
        if not self.config.webhook_url:
            for event in events:
                print(event.render_text())
            return
        lines = [event.render_text() for event in events]
        payload = {
            "msgtype": "text",
            "text": {"content": "\n".join(lines)},
        }
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            self.config.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10):
            return
