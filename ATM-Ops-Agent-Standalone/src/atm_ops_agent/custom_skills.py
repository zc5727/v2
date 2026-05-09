from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import shlex
from typing import Mapping


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_PLACEHOLDER_RE = re.compile(r"{([a-zA-Z0-9_]+)}")
_DANGEROUS_PATTERNS = (
    r"\brm\s+-rf\b",
    r"\bmkfs(\.| )",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\binit\s+0\b",
    r"\bdd\s+if=",
    r">\s*/etc/(passwd|shadow|ssh/sshd_config)",
    r"\buserdel\b",
    r"\bpasswd\b",
)


@dataclass(frozen=True, slots=True)
class CustomSkillParam:
    name: str
    label: str = ""
    required: bool = False
    default: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class CustomSkillSpec:
    name: str
    description: str
    mutating: bool
    command_template: str
    validation_template: str = ""
    params_hint: str = ""
    prompt_hint: str = ""
    params_schema: list[CustomSkillParam] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CustomSkillPreview:
    command: str
    missing_params: list[str]


class _DefaultContext(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


class CustomSkillStore:
    def __init__(self, root_dir: str | Path = "ops_data") -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.path = self.root_dir / "custom-skills.json"
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[CustomSkillSpec]:
        payload = self._load_payload()
        skills: list[CustomSkillSpec] = []
        for item in payload:
            item = dict(item)
            item["params_schema"] = [CustomSkillParam(**param) for param in item.get("params_schema", [])]
            skills.append(CustomSkillSpec(**item))
        return skills

    def get_skill(self, name: str) -> CustomSkillSpec | None:
        for item in self.list_skills():
            if item.name == name:
                return item
        return None

    def save_skill(self, spec: CustomSkillSpec) -> None:
        self._validate_spec(spec)
        payload = self._load_payload()
        remaining = [item for item in payload if item.get("name") != spec.name]
        record = asdict(spec)
        record["params_schema"] = [asdict(param) for param in spec.params_schema]
        remaining.append(record)
        remaining.sort(key=lambda item: item["name"])
        self.path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_payload(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []

    def _validate_spec(self, spec: CustomSkillSpec) -> None:
        if not _NAME_RE.match(spec.name):
            raise ValueError("skill name must match ^[a-z0-9][a-z0-9-]{1,62}$")
        if not spec.description.strip():
            raise ValueError("skill description is required")
        if not spec.command_template.strip():
            raise ValueError("command_template is required")
        _guard_command_template(spec.command_template)
        if spec.validation_template.strip():
            _guard_command_template(spec.validation_template)
        names = set()
        for param in spec.params_schema:
            if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", param.name):
                raise ValueError(f"invalid params_schema name: {param.name}")
            if param.name in names:
                raise ValueError(f"duplicate params_schema name: {param.name}")
            names.add(param.name)


def parse_params_schema(raw: str) -> list[CustomSkillParam]:
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"params_schema is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("params_schema must be a JSON array")
    params: list[CustomSkillParam] = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("name"):
            raise ValueError("each params_schema item must be an object with a name")
        params.append(
            CustomSkillParam(
                name=str(item["name"]).strip(),
                label=str(item.get("label", "")).strip(),
                required=bool(item.get("required", False)),
                default=str(item.get("default", "")).strip(),
                description=str(item.get("description", "")).strip(),
            )
        )
    return params


def render_params_from_schema(spec: CustomSkillSpec, params: Mapping[str, str]) -> dict[str, str]:
    merged = {str(key): str(value) for key, value in params.items()}
    for item in spec.params_schema:
        if not merged.get(item.name) and item.default:
            merged[item.name] = item.default
    return merged


def validate_params_against_schema(spec: CustomSkillSpec, params: Mapping[str, str]) -> dict[str, str]:
    merged = render_params_from_schema(spec, params)
    if not spec.params_schema:
        return merged
    allowed = {item.name for item in spec.params_schema}
    extras = sorted(key for key in merged if key not in allowed)
    if extras:
        raise ValueError(f"unexpected params for {spec.name}: {', '.join(extras)}")
    missing = [item.name for item in spec.params_schema if item.required and not merged.get(item.name, "").strip()]
    if missing:
        raise ValueError(f"missing required params for {spec.name}: {', '.join(missing)}")
    return merged


def preview_custom_skill_command(
    spec: CustomSkillSpec,
    *,
    target: str,
    service: str | None,
    params: Mapping[str, str],
) -> CustomSkillPreview:
    merged = render_params_from_schema(spec, params)
    missing = [item.name for item in spec.params_schema if item.required and not merged.get(item.name, "").strip()]
    command = _render_command(spec, target=target, service=service, params=merged, allow_missing=True)
    return CustomSkillPreview(command=command, missing_params=missing)


def build_custom_skill_command(
    spec: CustomSkillSpec,
    *,
    target: str,
    service: str | None,
    params: Mapping[str, str],
) -> str:
    merged = validate_params_against_schema(spec, params)
    return _render_command(spec, target=target, service=service, params=merged, allow_missing=False)


def _render_command(
    spec: CustomSkillSpec,
    *,
    target: str,
    service: str | None,
    params: Mapping[str, str],
    allow_missing: bool,
) -> str:
    context = _DefaultContext(
        {
            "target": target,
            "target_q": shlex.quote(target),
            "service": service or "",
            "service_q": shlex.quote(service or ""),
        }
    )
    for key, value in params.items():
        text = str(value)
        context[key] = text
        context[f"{key}_q"] = shlex.quote(text)
    placeholders = set(_PLACEHOLDER_RE.findall(spec.command_template + "\n" + spec.validation_template))
    if not allow_missing:
        unresolved = sorted(name for name in placeholders if name not in context)
        if unresolved:
            raise ValueError(f"missing placeholder values for {spec.name}: {', '.join(unresolved)}")
    try:
        command = spec.command_template.format_map(context).strip()
        validation = spec.validation_template.format_map(context).strip()
    except KeyError as exc:
        raise ValueError(f"unknown placeholder in custom skill template: {exc.args[0]}") from exc
    if not command:
        raise ValueError("rendered custom skill command is empty")
    full = f"set -e; {command}"
    if validation:
        full += f"; echo '### VALIDATION'; {validation}"
    return full


def _guard_command_template(command: str) -> None:
    lowered = command.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, lowered):
            raise ValueError(f"dangerous command pattern is not allowed: {pattern}")
