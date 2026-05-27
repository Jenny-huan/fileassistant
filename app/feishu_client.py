from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"


class FeishuError(Exception):
    pass


@dataclass(frozen=True)
class FeishuDocRef:
    doc_type: str
    token: str


def credentials_available() -> bool:
    return bool(os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"))


def parse_feishu_doc_url(url: str) -> FeishuDocRef:
    parsed = urlparse(url.strip())
    if not parsed.netloc or "feishu.cn" not in parsed.netloc:
        raise FeishuError("请输入飞书文档链接。")
    patterns = [
        (r"/docx/([A-Za-z0-9]+)", "docx"),
        (r"/docs/([A-Za-z0-9]+)", "doc"),
        (r"/wiki/([A-Za-z0-9]+)", "wiki"),
    ]
    for pattern, doc_type in patterns:
        match = re.search(pattern, parsed.path)
        if match:
            return FeishuDocRef(doc_type=doc_type, token=match.group(1))
    raise FeishuError("暂不支持此飞书链接类型，请使用 docx/docs/wiki 文档链接。")


def export_feishu_docx(url: str, poll_seconds: float = 1.0, max_attempts: int = 30) -> tuple[str, bytes]:
    if not credentials_available():
        raise FeishuError("缺少飞书应用凭证，请先设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET。")
    doc_ref = parse_feishu_doc_url(url)
    tenant_token = _get_tenant_access_token()
    ticket = _create_export_task(tenant_token, doc_ref)
    result = _poll_export_task(tenant_token, ticket, doc_ref.token, max_attempts, poll_seconds)
    file_token = result.get("file_token")
    if not file_token:
        raise FeishuError("飞书导出任务完成，但没有返回 file_token。")
    data = _download_exported_file(tenant_token, file_token)
    return f"feishu_{doc_ref.token}.docx", data


def _get_tenant_access_token() -> str:
    payload = {
        "app_id": os.environ["FEISHU_APP_ID"],
        "app_secret": os.environ["FEISHU_APP_SECRET"],
    }
    data = _json_request("/auth/v3/tenant_access_token/internal", payload=payload)
    token = data.get("tenant_access_token")
    if not token:
        raise FeishuError("无法获取飞书 tenant_access_token。")
    return token


def _create_export_task(tenant_token: str, doc_ref: FeishuDocRef) -> str:
    payload = {
        "file_extension": "docx",
        "token": doc_ref.token,
        "type": doc_ref.doc_type,
    }
    data = _json_request("/drive/v1/export_tasks", payload=payload, tenant_token=tenant_token)
    ticket = data.get("ticket") or data.get("data", {}).get("ticket")
    if not ticket:
        raise FeishuError(f"飞书导出任务创建失败：{data}")
    return ticket


def _poll_export_task(
    tenant_token: str,
    ticket: str,
    token: str,
    max_attempts: int,
    poll_seconds: float,
) -> dict:
    path = f"/drive/v1/export_tasks/{ticket}?token={token}"
    last = {}
    for _ in range(max_attempts):
        data = _json_request(path, tenant_token=tenant_token)
        last = data.get("result") or data.get("data", {}).get("result") or data
        job_status = last.get("job_status") or last.get("status")
        if job_status in {"success", 0, "0", "done"} or last.get("file_token"):
            return last
        if job_status in {"failed", "fail", 2, "2"}:
            raise FeishuError(f"飞书导出失败：{last}")
        time.sleep(poll_seconds)
    raise FeishuError(f"飞书导出超时：{last}")


def _download_exported_file(tenant_token: str, file_token: str) -> bytes:
    request = Request(
        f"{FEISHU_BASE_URL}/drive/v1/export_tasks/file/{file_token}/download",
        headers={"Authorization": f"Bearer {tenant_token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return response.read()
    except (HTTPError, URLError) as exc:
        raise FeishuError(f"飞书导出文件下载失败：{exc}") from exc


def _json_request(path: str, payload: dict | None = None, tenant_token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    method = "GET"
    body = None
    if tenant_token:
        headers["Authorization"] = f"Bearer {tenant_token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        method = "POST"
    request = Request(f"{FEISHU_BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError) as exc:
        raise FeishuError(f"飞书 API 请求失败：{exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FeishuError(f"飞书 API 返回非 JSON 数据：{raw[:200]}") from exc
    if data.get("code") not in (None, 0):
        raise FeishuError(f"飞书 API 返回错误：{data}")
    return data.get("data") or data

