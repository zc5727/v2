from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import threading
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _incident_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"INC-{stamp}-{uuid4().hex[:6]}"


@dataclass(slots=True)
class ActionRecord:
    action_id: str
    timestamp: str
    kind: str
    target: str
    action: str
    service: str | None
    reason: str | None
    command: str
    returncode: int
    action_success: bool
    issue_resolved: str
    headline: str
    risk_level: str = "low"
    approval_ticket: str | None = None
    operator: str | None = None
    findings: list[str] = field(default_factory=list)
    report_path: str | None = None


@dataclass(slots=True)
class IncidentRecord:
    incident_id: str
    created_at: str
    updated_at: str
    source: str
    title: str
    prompt: str
    target: str | None
    status: str
    resolution: str
    final_headline: str
    operator: str | None = None
    approval_ticket: str | None = None
    tags: list[str] = field(default_factory=list)
    report_paths: list[str] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)


@dataclass(slots=True)
class KnowledgeEntry:
    knowledge_id: str
    created_at: str
    incident_id: str
    title: str
    target: str | None
    resolution: str
    final_headline: str
    key_findings: list[str] = field(default_factory=list)
    action_digest: list[str] = field(default_factory=list)


class OpsStore:
    def __init__(self, root_dir: str | Path = "ops_data") -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.incidents_dir = self.root_dir / "incidents"
        self.history_path = self.root_dir / "action-history.jsonl"
        self.knowledge_path = self.root_dir / "knowledge.jsonl"
        self._lock = threading.Lock()
        self._ensure_dirs()

    def create_incident(
        self,
        *,
        source: str,
        title: str,
        prompt: str,
        target: str | None,
        status: str = "planned",
        operator: str | None = None,
        approval_ticket: str | None = None,
        tags: list[str] | None = None,
    ) -> IncidentRecord:
        record = IncidentRecord(
            incident_id=_incident_id(),
            created_at=_now(),
            updated_at=_now(),
            source=source,
            title=title,
            prompt=prompt,
            target=target,
            status=status,
            resolution="unknown",
            final_headline="",
            operator=operator,
            approval_ticket=approval_ticket,
            tags=list(tags or []),
        )
        self.save_incident(record)
        return record

    def save_incident(self, incident: IncidentRecord) -> None:
        incident.updated_at = _now()
        payload = self._incident_to_dict(incident)
        with self._lock:
            self._incident_path(incident.incident_id).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def load_incident(self, incident_id: str) -> IncidentRecord:
        payload = json.loads(self._incident_path(incident_id).read_text(encoding="utf-8"))
        return self._incident_from_dict(payload)

    def list_incidents(self, limit: int = 50) -> list[IncidentRecord]:
        files = sorted(self.incidents_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        incidents: list[IncidentRecord] = []
        for path in files[:limit]:
            payload = json.loads(path.read_text(encoding="utf-8"))
            incidents.append(self._incident_from_dict(payload))
        return incidents

    def append_action(self, incident_id: str, record: ActionRecord) -> IncidentRecord:
        incident = self.load_incident(incident_id)
        incident.actions.append(record)
        if record.report_path and record.report_path not in incident.report_paths:
            incident.report_paths.append(record.report_path)
        incident.final_headline = record.headline or incident.final_headline
        incident.resolution = record.issue_resolved or incident.resolution
        self.save_incident(incident)
        self._append_jsonl(self.history_path, {"incident_id": incident_id, **asdict(record)})
        return incident

    def finalize_incident(
        self,
        incident_id: str,
        *,
        status: str,
        resolution: str,
        final_headline: str,
        capture_knowledge: bool = False,
    ) -> IncidentRecord:
        incident = self.load_incident(incident_id)
        incident.status = status
        incident.resolution = resolution
        incident.final_headline = final_headline
        self.save_incident(incident)
        if capture_knowledge:
            self.capture_knowledge(incident)
        return incident

    def capture_knowledge(self, incident: IncidentRecord) -> KnowledgeEntry:
        key_findings: list[str] = []
        action_digest: list[str] = []
        for action in incident.actions:
            action_digest.append(
                f"{action.timestamp} {action.target} {action.action} service={action.service or '-'} resolved={action.issue_resolved}"
            )
            for item in action.findings:
                if item not in key_findings:
                    key_findings.append(item)
        entry = KnowledgeEntry(
            knowledge_id=f"KB-{uuid4().hex[:8]}",
            created_at=_now(),
            incident_id=incident.incident_id,
            title=incident.title,
            target=incident.target,
            resolution=incident.resolution,
            final_headline=incident.final_headline,
            key_findings=key_findings[:8],
            action_digest=action_digest[:12],
        )
        self._append_jsonl(self.knowledge_path, asdict(entry))
        return entry

    def list_knowledge(self, limit: int = 50) -> list[KnowledgeEntry]:
        entries = self._read_jsonl(self.knowledge_path)
        return [KnowledgeEntry(**item) for item in entries[-limit:]][::-1]

    def search_knowledge(self, query: str, limit: int = 5) -> list[KnowledgeEntry]:
        terms = self._terms(query)
        if not terms:
            return self.list_knowledge(limit=limit)
        scored: list[tuple[int, KnowledgeEntry]] = []
        for item in self.list_knowledge(limit=200):
            haystack = " ".join(
                [
                    item.title,
                    item.target or "",
                    item.resolution,
                    item.final_headline,
                    " ".join(item.key_findings),
                    " ".join(item.action_digest),
                ]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def search_incidents(self, query: str, limit: int = 5) -> list[IncidentRecord]:
        terms = self._terms(query)
        incidents = self.list_incidents(limit=200)
        if not terms:
            return incidents[:limit]
        scored: list[tuple[int, IncidentRecord]] = []
        for item in incidents:
            haystack = " ".join(
                [
                    item.title,
                    item.prompt,
                    item.target or "",
                    item.status,
                    item.resolution,
                    item.final_headline,
                ]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _ensure_dirs(self) -> None:
        self.incidents_dir.mkdir(parents=True, exist_ok=True)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _incident_path(self, incident_id: str) -> Path:
        return self.incidents_dir / f"{incident_id}.json"

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        items: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
        return items

    def _terms(self, text: str) -> list[str]:
        return [item for item in re.findall(r"[a-z0-9_\-/.:]+|[\u4e00-\u9fff]{2,}", text.lower()) if item]

    def _incident_to_dict(self, incident: IncidentRecord) -> dict[str, Any]:
        data = asdict(incident)
        data["actions"] = [asdict(item) for item in incident.actions]
        return data

    def _incident_from_dict(self, payload: dict[str, Any]) -> IncidentRecord:
        return IncidentRecord(
            incident_id=payload["incident_id"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            source=payload["source"],
            title=payload["title"],
            prompt=payload["prompt"],
            target=payload.get("target"),
            status=payload["status"],
            resolution=payload.get("resolution", "unknown"),
            final_headline=payload.get("final_headline", ""),
            operator=payload.get("operator"),
            approval_ticket=payload.get("approval_ticket"),
            tags=list(payload.get("tags", [])),
            report_paths=list(payload.get("report_paths", [])),
            actions=[ActionRecord(**item) for item in payload.get("actions", [])],
        )
