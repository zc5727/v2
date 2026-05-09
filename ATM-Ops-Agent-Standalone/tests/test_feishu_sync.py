from __future__ import annotations

from pathlib import Path

from atm_ops_agent.config import FeishuConfig
from atm_ops_agent.feishu import FeishuDocClient


def test_sync_document_creates_new_doc_and_snapshot(tmp_path):
    client = FeishuDocClient(
        FeishuConfig(
            enabled=True,
            app_id="app-id",
            app_secret="app-secret",
            import_dir=str(tmp_path),
        )
    )

    calls: list[tuple[str, str, object]] = []

    def fake_request(path: str, *, method: str = "GET", payload=None, bearer=None):
        calls.append((path, method, payload))
        if path == "/open-apis/docx/v1/documents" and method == "POST":
            return {"code": 0, "data": {"document": {"document_id": "doc-new", "url": "https://example/doc-new"}}}
        if path == "/open-apis/docx/v1/documents/doc-new" and method == "GET":
            return {"code": 0, "data": {"document": {"document_id": "doc-new", "title": "巡检记录"}}}
        if path.endswith("/children?page_size=500") and method == "GET":
            return {"code": 0, "data": {"items": []}}
        if "/children" in path and method == "POST":
            return {"code": 0, "data": {}}
        raise AssertionError(f"unexpected request: path={path} method={method}")

    client._tenant_access_token = lambda: "tenant-token"  # type: ignore[method-assign]
    client._request_json = fake_request  # type: ignore[method-assign]

    result = client.sync_document(title="巡检记录", content="# 标题\n- 第一行", url_or_token=None, append=False)

    assert result.token == "doc-new"
    assert result.mode == "create"
    assert Path(result.saved_path or "").exists()
    assert any(path == "/open-apis/docx/v1/documents" and method == "POST" for path, method, _ in calls)


def test_sync_document_appends_existing_raw_content(tmp_path):
    client = FeishuDocClient(
        FeishuConfig(
            enabled=True,
            app_id="app-id",
            app_secret="app-secret",
            import_dir=str(tmp_path),
        )
    )

    posted_payloads: list[object] = []

    def fake_request(path: str, *, method: str = "GET", payload=None, bearer=None):
        if path == "/open-apis/docx/v1/documents/doc-existing" and method == "GET":
            return {"code": 0, "data": {"document": {"document_id": "doc-existing", "title": "事故记录"}}}
        if path.endswith("/raw_content") and method == "GET":
            return {"code": 0, "data": {"content": "已有内容"}}
        if path.endswith("/children?page_size=500") and method == "GET":
            return {"code": 0, "data": {"items": [{"block_id": "block-1"}]}}
        if path.endswith("/children/batch_delete") and method == "DELETE":
            return {"code": 0, "data": {}}
        if "/children" in path and method == "POST":
            posted_payloads.append(payload)
            return {"code": 0, "data": {}}
        raise AssertionError(f"unexpected request: path={path} method={method}")

    client._tenant_access_token = lambda: "tenant-token"  # type: ignore[method-assign]
    client._request_json = fake_request  # type: ignore[method-assign]

    result = client.sync_document(
        title="事故记录",
        content="新增结论",
        url_or_token="doc-existing",
        append=True,
    )

    assert result.mode == "append"
    serialized = str(posted_payloads)
    assert "已有内容" in serialized
    assert "新增结论" in serialized
