from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from atm_ops_agent.config import LLMConfig


@dataclass(slots=True)
class LLMDiagnosis:
    ok: bool
    provider: str
    model: str
    base_url: str
    key_present: bool
    status: str
    headline: str
    detail: str
    recommendation: str
    raw_error: str = ""


def diagnose_llm(config: LLMConfig) -> LLMDiagnosis:
    provider = (config.provider or "").strip().lower()
    provider_label = _provider_label(provider)
    if not config.enabled:
        return LLMDiagnosis(
            ok=False,
            provider=provider_label,
            model=config.model,
            base_url=config.base_url,
            key_present=bool(config.api_key),
            status="disabled",
            headline="大模型功能当前未启用",
            detail="inventory.toml 里的 [llm].enabled 为 false。",
            recommendation="把 [llm].enabled 改成 true，然后重新启动本地 agent。",
        )
    if not llm_ready(config):
        return LLMDiagnosis(
            ok=False,
            provider=provider_label,
            model=config.model,
            base_url=config.base_url,
            key_present=False,
            status="missing_api_key",
            headline="缺少模型 API 凭据",
            detail="阿里百炼需要 API Key，但运行时没有读取到可用凭据。",
            recommendation="优先配置环境变量 BAILIAN_API_KEY、ALIYUN_BAILIAN_API_KEY 或 DASHSCOPE_API_KEY；如果想本地运行，可以切到 Ollama。",
        )

    try:
        reply = chat_completion(
            config,
            messages=[
                {"role": "system", "content": "你是连通性检查助手，只能回答 ok。"},
                {"role": "user", "content": "请只回复 ok"},
            ],
            json_mode=False,
            max_tokens=12,
        )
        return LLMDiagnosis(
            ok=True,
            provider=provider_label,
            model=config.model,
            base_url=config.base_url,
            key_present=bool(config.api_key),
            status="ok",
            headline="大模型调用正常",
            detail=f"连通性测试已成功，模型返回：{reply[:120]}",
            recommendation="现在可以继续在对话助手、规划器和解释器里使用大模型。",
        )
    except HTTPError as exc:
        body = _read_http_error(exc)
        status, headline, recommendation = _classify_http_error(exc.code, body, provider)
        return LLMDiagnosis(
            ok=False,
            provider=provider_label,
            model=config.model,
            base_url=config.base_url,
            key_present=bool(config.api_key),
            status=status,
            headline=headline,
            detail=body or str(exc),
            recommendation=recommendation,
            raw_error=str(exc),
        )
    except URLError as exc:
        return LLMDiagnosis(
            ok=False,
            provider=provider_label,
            model=config.model,
            base_url=config.base_url,
            key_present=bool(config.api_key),
            status="network_error",
            headline="模型服务不可达",
            detail=str(exc.reason or exc),
            recommendation="检查网络、代理或本地模型服务是否已启动；如果是 Ollama，请先执行 ollama serve。",
            raw_error=str(exc),
        )
    except Exception as exc:
        return LLMDiagnosis(
            ok=False,
            provider=provider_label,
            model=config.model,
            base_url=config.base_url,
            key_present=bool(config.api_key),
            status="unknown_error",
            headline="模型调用失败",
            detail=str(exc),
            recommendation="先用诊断结果里的 detail 排查；若是云端模型，也建议检查额度、限流和模型名。",
            raw_error=str(exc),
        )


def chat_completion(
    config: LLMConfig,
    *,
    messages: list[dict[str, str]],
    json_mode: bool,
    max_tokens: int | None = None,
) -> str:
    provider = (config.provider or "").strip().lower()
    if provider == "ollama":
        return _ollama_chat_completion(config, messages=messages, json_mode=json_mode)
    return _openai_compatible_chat_completion(config, messages=messages, json_mode=json_mode, max_tokens=max_tokens)


def _provider_label(provider: str) -> str:
    if provider in {"aliyun-bailian", "bailian", "dashscope"}:
        return "阿里百炼"
    if provider == "ollama":
        return "Ollama"
    return provider or "unknown"


def llm_ready(config: LLMConfig) -> bool:
    provider = (config.provider or "").strip().lower()
    if not config.enabled:
        return False
    if provider == "ollama":
        return True
    return bool(config.api_key)


def _openai_compatible_chat_completion(
    config: LLMConfig,
    *,
    messages: list[dict[str, str]],
    json_mode: bool,
    max_tokens: int | None,
) -> str:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    body = _request_json(
        config,
        path="/chat/completions",
        payload=payload,
        headers={"Authorization": f"Bearer {config.api_key}"},
    )
    return str(body["choices"][0]["message"]["content"]).strip()


def _ollama_chat_completion(
    config: LLMConfig,
    *,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> str:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"
    body = _request_json(config, path="/api/chat", payload=payload, headers={})
    message = body.get("message", {}) if isinstance(body, dict) else {}
    return str(message.get("content", "")).strip()


def _request_json(
    config: LLMConfig,
    *,
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    url = config.base_url.rstrip("/") + path
    merged_headers = {"Content-Type": "application/json", **headers}
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=merged_headers,
        method="POST",
    )
    with urlopen(request, timeout=config.timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _read_http_error(exc: HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return str(exc)
    try:
        parsed = json.loads(raw)
    except JSONDecodeError:
        return raw.strip()
    if isinstance(parsed, dict):
        if parsed.get("error"):
            return json.dumps(parsed["error"], ensure_ascii=False)
        return json.dumps(parsed, ensure_ascii=False)
    return raw.strip()


def _classify_http_error(code: int, body: str, provider: str) -> tuple[str, str, str]:
    lowered = body.lower()
    if code == 401 or "invalid api key" in lowered or "unauthorized" in lowered:
        return (
            "invalid_api_key",
            "模型凭据无效",
            "检查 API Key 是否填错、过期，或改成环境变量后重新启动服务。",
        )
    if code == 403 and ("quota" in lowered or "free" in lowered or "allocationquota" in lowered):
        return (
            "quota_exhausted",
            "模型额度可能已用完",
            "如果想继续免费使用，推荐切到 Ollama 本地模型；否则去云端控制台检查额度和计费开关。",
        )
    if code == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return (
            "rate_limited",
            "模型调用被限流",
            "降低请求频率，或更换模型/账号；如果是本地 Ollama，则检查是否同时有过多并发请求。",
        )
    if code == 404 and provider == "ollama":
        return (
            "ollama_api_missing",
            "没有连到可用的 Ollama 接口",
            "确认本机已安装并启动 Ollama，默认地址应为 http://127.0.0.1:11434 。",
        )
    if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return (
            "model_not_found",
            "模型名不可用",
            "检查 inventory.toml 中的 model 是否正确；如果是 Ollama，先执行 ollama pull 对应模型。",
        )
    return (
        "http_error",
        f"模型服务返回 HTTP {code}",
        "检查接口地址、模型名、额度和权限；如果想避免云端额度问题，可以切到 Ollama 本地模型。",
    )


def build_ollama_config_snippet(model: str = "qwen3:8b") -> str:
    return (
        "[llm]\n"
        'enabled = true\n'
        'provider = "ollama"\n'
        'base_url = "http://127.0.0.1:11434"\n'
        'api_key = ""\n'
        f'model = "{model}"\n'
        "timeout_seconds = 60\n"
    )
