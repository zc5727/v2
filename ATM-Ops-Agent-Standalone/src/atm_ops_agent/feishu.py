from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from atm_ops_agent.config import FeishuConfig


_DOC_PATH_RE = re.compile(r"/docx?/([A-Za-z0-9]+)")
_WIKI_PATH_RE = re.compile(r"/wiki/([A-Za-z0-9]+)")


@dataclass(slots=True)
class FeishuDocument:
    token: str
    doc_type: str
    title: str
    raw_content: str
    source_url: str
    imported_at: str
    saved_path: str | None = None


@dataclass(slots=True)
class FeishuKnowledgeHit:
    title: str
    target: str | None
    resolution: str
    final_headline: str
    raw_content: str
    saved_path: str | None = None


@dataclass(slots=True)
class FeishuSyncResult:
    token: str
    doc_type: str
    title: str
    source_url: str
    mode: str
    updated_at: str
    saved_path: str | None = None


@dataclass(slots=True)
class FeishuUserToken:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    token_type: str = "Bearer"
    scope: str = ""
    obtained_at: str = ""


class FeishuDocClient:
    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self.user_token_path = Path.home() / ".agenthub" / "feishu-user-token.json"

    def import_document(self, url_or_token: str) -> FeishuDocument:
        tenant_token = self._tenant_access_token()
        token, doc_type, normalized_url = self._parse_doc_reference(url_or_token, tenant_token)
        title = self._fetch_title(token, doc_type, tenant_token)
        content = self._fetch_raw_content(token, doc_type, tenant_token)
        document = FeishuDocument(
            token=token,
            doc_type=doc_type,
            title=title,
            raw_content=content,
            source_url=normalized_url,
            imported_at=datetime.now().isoformat(timespec="seconds"),
        )
        saved = self._save_document(document)
        document.saved_path = str(saved)
        return document

    def sync_document(
        self,
        *,
        title: str,
        content: str,
        url_or_token: str | None = None,
        append: bool = False,
        use_user_token: bool = False,
    ) -> FeishuSyncResult:
        bearer = self._user_access_token() if use_user_token else self._tenant_access_token()
        if url_or_token:
            token, doc_type, normalized_url = self._parse_doc_reference(url_or_token, bearer)
            if doc_type != "docx":
                raise ValueError("Feishu write-back currently supports docx documents only")
        else:
            token, normalized_url = self._create_docx_document(title=title, bearer=bearer)
            doc_type = "docx"

        existing = self._fetch_raw_content(token, doc_type, bearer) if append else ""
        merged = self._merge_content(existing, content, append=append)
        self._replace_docx_content(token, title=title, markdown=merged, bearer=bearer)
        result = FeishuSyncResult(
            token=token,
            doc_type=doc_type,
            title=title,
            source_url=normalized_url,
            mode="append" if append else ("create" if not url_or_token else "overwrite"),
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )
        saved = self._save_sync_snapshot(result, merged)
        result.saved_path = str(saved)
        return result

    def save_user_access_token(self, code: str) -> dict[str, str]:
        app_access_token = self._app_access_token()
        body = self._request_json(
            "/open-apis/authen/v1/access_token",
            method="POST",
            payload={"grant_type": "authorization_code", "code": code},
            bearer=app_access_token,
        )
        data = body.get("data", {})
        token = FeishuUserToken(
            access_token=str(data.get("access_token", "")).strip(),
            refresh_token=str(data.get("refresh_token", "")).strip(),
            expires_in=int(data.get("expires_in", 0) or 0),
            refresh_expires_in=int(data.get("refresh_expires_in", 0) or 0),
            token_type=str(data.get("token_type", "Bearer") or "Bearer"),
            scope=str(data.get("scope", "") or ""),
            obtained_at=datetime.now().isoformat(timespec="seconds"),
        )
        if not token.access_token or not token.refresh_token:
            raise ValueError(f"failed to obtain user_access_token: {body}")
        self._save_user_token(token)
        return {
            "access_token_prefix": token.access_token[:18],
            "refresh_token_prefix": token.refresh_token[:18],
            "obtained_at": token.obtained_at,
        }

    def refresh_user_access_token(self) -> dict[str, str]:
        current = self._load_user_token()
        app_access_token = self._app_access_token()
        body = self._request_json(
            "/open-apis/authen/v1/refresh_access_token",
            method="POST",
            payload={"grant_type": "refresh_token", "refresh_token": current.refresh_token},
            bearer=app_access_token,
        )
        data = body.get("data", {})
        token = FeishuUserToken(
            access_token=str(data.get("access_token", "")).strip(),
            refresh_token=str(data.get("refresh_token", "")).strip() or current.refresh_token,
            expires_in=int(data.get("expires_in", 0) or 0),
            refresh_expires_in=int(data.get("refresh_expires_in", 0) or current.refresh_expires_in),
            token_type=str(data.get("token_type", "Bearer") or "Bearer"),
            scope=str(data.get("scope", current.scope) or ""),
            obtained_at=datetime.now().isoformat(timespec="seconds"),
        )
        if not token.access_token:
            raise ValueError(f"failed to refresh user_access_token: {body}")
        self._save_user_token(token)
        return {
            "access_token_prefix": token.access_token[:18],
            "refresh_token_prefix": token.refresh_token[:18],
            "obtained_at": token.obtained_at,
        }

    def list_imported_documents(self, limit: int = 50) -> list[FeishuDocument]:
        output_dir = Path(self.config.import_dir).expanduser().resolve()
        if not output_dir.exists():
            return []
        docs: list[FeishuDocument] = []
        files = sorted(output_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
        for path in files[:limit]:
            parsed = self._read_saved_document(path)
            if parsed:
                docs.append(parsed)
        return docs

    def search_imported_documents(self, query: str, limit: int = 5) -> list[FeishuKnowledgeHit]:
        terms = [item for item in re.findall(r"[a-z0-9_\-/.:]+|[\u4e00-\u9fff]{2,}", query.lower()) if item]
        output_dir = Path(self.config.import_dir).expanduser().resolve()
        if not output_dir.exists():
            return []
        scored: list[tuple[int, FeishuKnowledgeHit]] = []
        for path in output_dir.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            haystack = text.lower()
            score = sum(1 for term in terms if term in haystack) if terms else 1
            if score <= 0:
                continue
            doc = self._read_saved_document(path)
            if not doc:
                continue
            headline = ""
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("- ") and stripped != "## Raw Content":
                    headline = stripped[:120]
                    break
            scored.append(
                (
                    score,
                    FeishuKnowledgeHit(
                        title=doc.title,
                        target="feishu-doc",
                        resolution="reference",
                        final_headline=headline or "imported Feishu document",
                        raw_content=text[:1200],
                        saved_path=doc.saved_path,
                    ),
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _tenant_access_token(self) -> str:
        if not self.config.enabled:
            raise ValueError("feishu integration is disabled in config")
        if not self.config.app_id or not self.config.app_secret:
            raise ValueError("feishu app_id/app_secret is not configured")
        payload = {
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret,
        }
        body = self._request_json(
            "/open-apis/auth/v3/tenant_access_token/internal",
            method="POST",
            payload=payload,
        )
        token = str(body.get("tenant_access_token", "")).strip()
        if not token:
            raise ValueError(f"failed to obtain feishu tenant access token: {body}")
        return token

    def _fetch_title(self, token: str, doc_type: str, tenant_token: str) -> str:
        path = f"/open-apis/docx/v1/documents/{token}" if doc_type == "docx" else f"/open-apis/docs/v2/documents/{token}"
        body = self._request_json(path, bearer=tenant_token)
        if doc_type == "docx":
            return str(body.get("data", {}).get("document", {}).get("title", "")).strip() or token
        return str(body.get("data", {}).get("document", {}).get("title", "")).strip() or token

    def _fetch_raw_content(self, token: str, doc_type: str, tenant_token: str) -> str:
        path = (
            f"/open-apis/docx/v1/documents/{token}/raw_content"
            if doc_type == "docx"
            else f"/open-apis/docs/v2/documents/{token}/raw_content"
        )
        body = self._request_json(path, bearer=tenant_token)
        data = body.get("data", {})
        content = str(data.get("content", "")).strip()
        if not content:
            raise ValueError(f"feishu document raw content is empty for token={token}")
        return content

    def _create_docx_document(self, *, title: str, bearer: str) -> tuple[str, str]:
        body = self._request_json(
            "/open-apis/docx/v1/documents",
            method="POST",
            payload={"title": title},
            bearer=bearer,
        )
        data = body.get("data", {})
        document = data.get("document", {}) if isinstance(data, dict) else {}
        token = str(document.get("document_id", "")).strip()
        url = str(document.get("url", "")).strip()
        if not token:
            raise ValueError(f"failed to create feishu docx document: {body}")
        return token, url or f"https://open.feishu.cn/document/client-docx/{token}"

    def _replace_docx_content(self, token: str, *, title: str, markdown: str, bearer: str) -> None:
        meta = self._request_json(f"/open-apis/docx/v1/documents/{token}", bearer=bearer)
        document = meta.get("data", {}).get("document", {})
        root_block_id = str(document.get("document_id", "")).strip() or token
        old_children = self._list_block_children(token, root_block_id, bearer)
        if old_children:
            self._request_json(
                f"/open-apis/docx/v1/documents/{token}/blocks/{root_block_id}/children/batch_delete",
                method="DELETE",
                payload={"children": old_children},
                bearer=bearer,
            )
        blocks = self._markdown_to_feishu_blocks(markdown)
        if not blocks:
            blocks = [self._paragraph_block(title)]
        for chunk in self._chunk(blocks, 50):
            self._request_json(
                f"/open-apis/docx/v1/documents/{token}/blocks/{root_block_id}/children?document_revision_id=-1",
                method="POST",
                payload={"children": chunk, "index": -1},
                bearer=bearer,
            )

    def _list_block_children(self, token: str, block_id: str, bearer: str) -> list[str]:
        body = self._request_json(
            f"/open-apis/docx/v1/documents/{token}/blocks/{block_id}/children?page_size=500",
            bearer=bearer,
        )
        items = body.get("data", {}).get("items", [])
        if not isinstance(items, list):
            return []
        return [str(item.get("block_id", "")).strip() for item in items if isinstance(item, dict) and item.get("block_id")]

    def _merge_content(self, existing: str, new_content: str, *, append: bool) -> str:
        cleaned_new = new_content.strip()
        if not append or not existing.strip():
            return cleaned_new
        return existing.rstrip() + "\n\n---\n\n" + cleaned_new

    def _markdown_to_feishu_blocks(self, markdown: str) -> list[dict[str, object]]:
        blocks: list[dict[str, object]] = []
        for raw_line in markdown.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            normalized = re.sub(r"^#{1,3}\s+", "", line).strip()
            normalized = re.sub(r"^\d+\.\s+", "", normalized)
            normalized = normalized[2:].strip() if normalized.startswith("- ") else normalized
            blocks.append(self._paragraph_block(normalized))
        return blocks

    def _paragraph_block(self, text: str) -> dict[str, object]:
        return {
            "block_type": 2,
            "text": {
                "elements": [self._text_run(text)],
            },
        }

    @staticmethod
    def _text_run(text: str) -> dict[str, object]:
        return {
            "text_run": {
                "content": text,
            }
        }

    @staticmethod
    def _chunk(items: list[dict[str, object]], size: int) -> list[list[dict[str, object]]]:
        return [items[index : index + size] for index in range(0, len(items), size)]

    def _save_document(self, document: FeishuDocument) -> Path:
        output_dir = Path(self.config.import_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", document.title).strip("-") or document.token
        path = output_dir / f"{safe_title}-{document.token}.md"
        lines = [
            f"# {document.title}",
            "",
            f"- token: `{document.token}`",
            f"- doc_type: `{document.doc_type}`",
            f"- imported_at: `{document.imported_at}`",
            f"- source_url: {document.source_url}",
            "",
            "## Raw Content",
            "",
            document.raw_content,
            "",
        ]
        content = "\n".join(lines)
        try:
            path.write_text(content, encoding="utf-8")
            return path
        except OSError:
            fallback = output_dir / f"feishu-doc-{document.token}.md"
            fallback.write_text(content, encoding="utf-8")
            return fallback

    def _save_sync_snapshot(self, result: FeishuSyncResult, content: str) -> Path:
        output_dir = Path(self.config.import_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", result.title).strip("-") or result.token
        path = output_dir / f"{safe_title}-{result.token}-synced.md"
        lines = [
            f"# {result.title}",
            "",
            f"- token: `{result.token}`",
            f"- doc_type: `{result.doc_type}`",
            f"- mode: `{result.mode}`",
            f"- updated_at: `{result.updated_at}`",
            f"- source_url: {result.source_url}",
            "",
            "## Synced Content",
            "",
            content,
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        bearer: str | None = None,
    ) -> dict[str, object]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.config.base_url.rstrip("/") + path,
            data=data,
            headers=headers,
            method=method,
        )
        with urlopen(request, timeout=self.config.timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        code = int(body.get("code", 0) or 0)
        if code not in (0, 200):
            raise ValueError(f"feishu api error path={path} code={code} body={body}")
        return body

    def _app_access_token(self) -> str:
        if not self.config.app_id or not self.config.app_secret:
            raise ValueError("feishu app_id/app_secret is not configured")
        body = self._request_json(
            "/open-apis/auth/v3/app_access_token/internal",
            method="POST",
            payload={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
        )
        token = str(body.get("app_access_token", "")).strip()
        if not token:
            raise ValueError(f"failed to obtain app_access_token: {body}")
        return token

    def _user_access_token(self) -> str:
        env_token = str(__import__("os").getenv("FEISHU_USER_ACCESS_TOKEN") or "").strip()
        if env_token:
            return env_token
        token = self._load_user_token()
        if token.access_token:
            return token.access_token
        raise ValueError("user_access_token is not configured; run Feishu user auth first")

    def _load_user_token(self) -> FeishuUserToken:
        if not self.user_token_path.exists():
            raise ValueError(f"user token file not found: {self.user_token_path}")
        payload = json.loads(self.user_token_path.read_text(encoding="utf-8"))
        return FeishuUserToken(
            access_token=str(payload.get("access_token", "")).strip(),
            refresh_token=str(payload.get("refresh_token", "")).strip(),
            expires_in=int(payload.get("expires_in", 0) or 0),
            refresh_expires_in=int(payload.get("refresh_expires_in", 0) or 0),
            token_type=str(payload.get("token_type", "Bearer") or "Bearer"),
            scope=str(payload.get("scope", "") or ""),
            obtained_at=str(payload.get("obtained_at", "") or ""),
        )

    def _save_user_token(self, token: FeishuUserToken) -> None:
        self.user_token_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_token_path.write_text(
            json.dumps(
                {
                    "access_token": token.access_token,
                    "refresh_token": token.refresh_token,
                    "expires_in": token.expires_in,
                    "refresh_expires_in": token.refresh_expires_in,
                    "token_type": token.token_type,
                    "scope": token.scope,
                    "obtained_at": token.obtained_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _parse_doc_reference(self, value: str, tenant_token: str | None = None) -> tuple[str, str, str]:
        raw = value.strip()
        if not raw:
            raise ValueError("feishu document url or token is required")
        if raw.startswith("http://") or raw.startswith("https://"):
            parsed = urlparse(raw)
            wiki_match = _WIKI_PATH_RE.search(parsed.path)
            if wiki_match:
                if not tenant_token:
                    raise ValueError("tenant token is required to resolve wiki links")
                return self._resolve_wiki_node(wiki_match.group(1), tenant_token, raw)
            match = _DOC_PATH_RE.search(parsed.path)
            if match:
                token = match.group(1)
                doc_type = "docx" if "/docx/" in parsed.path else "doc"
                return token, doc_type, raw
            query = parse_qs(parsed.query)
            if "token" in query and query["token"]:
                return str(query["token"][0]), "docx", raw
            raise ValueError("unsupported feishu doc url, expected /docx/<token> or /docs/<token>")
        return raw, "docx", raw

    def _resolve_wiki_node(self, wiki_token: str, tenant_token: str, source_url: str) -> tuple[str, str, str]:
        body = self._request_json(
            f"/open-apis/wiki/v2/spaces/get_node?token={wiki_token}",
            bearer=tenant_token,
        )
        node = body.get("data", {}).get("node", {})
        obj_token = str(node.get("obj_token", "")).strip()
        obj_type = str(node.get("obj_type", "")).strip().lower()
        if not obj_token or not obj_type:
            raise ValueError(f"failed to resolve wiki node: {body}")
        if obj_type not in {"doc", "docx"}:
            raise ValueError(f"wiki node resolved to unsupported obj_type={obj_type}; current version supports doc/docx only")
        return obj_token, obj_type, source_url

    def _read_saved_document(self, path: Path) -> FeishuDocument | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        lines = text.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else path.stem
        meta: dict[str, str] = {}
        for line in lines[2:8]:
            if line.startswith("- ") and ":" in line:
                key, value = line[2:].split(":", 1)
                meta[key.strip()] = value.strip().strip("`")
        raw_content = ""
        if "## Raw Content" in text:
            raw_content = text.split("## Raw Content", 1)[1].strip()
        return FeishuDocument(
            token=meta.get("token", path.stem),
            doc_type=meta.get("doc_type", "docx"),
            title=title or path.stem,
            raw_content=raw_content,
            source_url=meta.get("source_url", ""),
            imported_at=meta.get("imported_at", ""),
            saved_path=str(path),
        )
