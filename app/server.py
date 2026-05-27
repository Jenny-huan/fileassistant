from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default
import json
import os
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from app.config import MAX_UPLOAD_BYTES, ensure_storage_dirs
from app.processor import (
    DEFAULT_WIDTH_RATIO,
    DEFAULT_ALIGNMENT,
    ProcessingError,
    get_job_file,
    inspect_upload,
    process_existing_job,
    process_upload,
)
from app.pdf_processor import (
    PdfProcessingError,
    get_pdf_job_file,
    inspect_pdf_upload,
    process_existing_pdf_job,
    process_pdf_upload,
)
from app.feishu_client import FeishuError, credentials_available, export_feishu_docx, parse_feishu_doc_url


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
FEISHU_ENABLED = False


class AppHandler(BaseHTTPRequestHandler):
    server_version = "DocxImageOrganizer/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(INDEX_HTML)
            return
        if path == "/health":
            self._send_json({"status": "ok"})
            return
        if path == "/api/feishu/status" and FEISHU_ENABLED:
            configured = credentials_available()
            self._send_json(
                {
                    "configured": configured,
                    "available": configured,
                    "message": "飞书导入已启用。" if configured else "当前未启用飞书导入。请先在飞书中下载为 Word，然后上传 .docx 文件。",
                    "setup_hint": "管理员可配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 启用飞书导入。",
                }
            )
            return
        if path.startswith("/api/docx/result/"):
            self._send_job_file(path.removeprefix("/api/docx/result/"), "output", DOCX_MIME)
            return
        if path.startswith("/api/docx/backup/"):
            self._send_job_file(path.removeprefix("/api/docx/backup/"), "backup", DOCX_MIME)
            return
        if path.startswith("/api/docx/report/"):
            self._send_job_file(path.removeprefix("/api/docx/report/"), "report", "application/json")
            return
        if path.startswith("/api/pdf/result/"):
            self._send_pdf_job_file(path.removeprefix("/api/pdf/result/"), "output", PDF_MIME)
            return
        if path.startswith("/api/pdf/backup/"):
            self._send_pdf_job_file(path.removeprefix("/api/pdf/backup/"), "backup", PDF_MIME)
            return
        if path.startswith("/api/pdf/report/"):
            self._send_pdf_job_file(path.removeprefix("/api/pdf/report/"), "report", "application/json")
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        enabled_paths = {"/api/docx/inspect", "/api/docx/format", "/api/pdf/inspect", "/api/pdf/format"}
        if FEISHU_ENABLED:
            enabled_paths.add("/api/feishu/import")
        if path not in enabled_paths:
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "Invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
            return
        if content_length > MAX_UPLOAD_BYTES + 1024 * 1024:
            self._send_json({"error": "Request exceeds the 50MB upload limit."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self._send_json({"error": "Expected multipart/form-data upload."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            parts = self._parse_multipart(content_type, content_length)
        except ProcessingError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/docx/inspect":
            upload = parts.get("file")
            if upload is None:
                self._send_json({"error": "Missing file field."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                report = inspect_upload(upload["filename"], upload["data"])
            except ProcessingError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(report)
            return
        if path == "/api/pdf/inspect":
            upload = parts.get("file")
            if upload is None:
                self._send_json({"error": "Missing file field."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                report = inspect_pdf_upload(upload["filename"], upload["data"])
            except PdfProcessingError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(report)
            return

        if path == "/api/pdf/format":
            try:
                if "job_id" in parts:
                    report = process_existing_pdf_job(parts["job_id"]["value"])
                else:
                    upload = parts.get("file")
                    if upload is None:
                        self._send_json({"error": "Missing file field or job_id."}, HTTPStatus.BAD_REQUEST)
                        return
                    report = process_pdf_upload(upload["filename"], upload["data"])
            except (PdfProcessingError, FileNotFoundError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(report)
            return

        if path == "/api/feishu/import":
            link = parts.get("url", {}).get("value", "").strip()
            if not link:
                self._send_json({"error": "Missing Feishu document URL."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                parse_feishu_doc_url(link)
                filename, data = export_feishu_docx(link)
                report = inspect_upload(filename, data)
                report["source"] = {
                    "type": "feishu",
                    "url": link,
                    "note": "Feishu document was exported as .docx and is ready for the Word/WPS image workflow.",
                }
            except FeishuError as exc:
                configured = credentials_available()
                self._send_json(
                    {
                        "error": "飞书导入当前不可用。" if not configured else str(exc),
                        "configured": configured,
                        "message": "请先在飞书中下载为 Word，然后上传 .docx 文件。" if not configured else str(exc),
                        "setup_hint": "管理员可配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 启用飞书导入。",
                    },
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json(report)
            return

        try:
            ratio = float(parts.get("max_width_ratio", {}).get("value", DEFAULT_WIDTH_RATIO))
        except (TypeError, ValueError):
            self._send_json({"error": "Invalid max_width_ratio."}, HTTPStatus.BAD_REQUEST)
            return
        alignment = parts.get("alignment", {}).get("value", DEFAULT_ALIGNMENT)
        try:
            if "job_id" in parts:
                report = process_existing_job(parts["job_id"]["value"], ratio, alignment)
            else:
                upload = parts.get("file")
                if upload is None:
                    self._send_json({"error": "Missing file field or job_id."}, HTTPStatus.BAD_REQUEST)
                    return
                report = process_upload(upload["filename"], upload["data"], ratio, alignment)
        except (ProcessingError, FileNotFoundError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(report)

    def log_message(self, format: str, *args) -> None:
        try:
            print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args))
        except (OSError, ValueError):
            pass

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_pdf_job_file(self, raw_job_id: str, kind: str, content_type: str) -> None:
        try:
            path = get_pdf_job_file(unquote(raw_job_id), kind)
        except FileNotFoundError:
            self._send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{Path(path).name}"')
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_job_file(self, raw_job_id: str, kind: str, content_type: str) -> None:
        try:
            path = get_job_file(unquote(raw_job_id), kind)
        except FileNotFoundError:
            self._send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{Path(path).name}"')
        self.end_headers()
        self.wfile.write(data)

    def _parse_multipart(self, content_type: str, content_length: int) -> dict:
        body = self.rfile.read(content_length)
        message = BytesParser(policy=default).parsebytes(
            (
                f"Content-Type: {content_type}\r\n"
                "MIME-Version: 1.0\r\n"
                "\r\n"
            ).encode("utf-8")
            + body
        )
        if not message.is_multipart():
            raise ProcessingError("Invalid multipart request.")
        parts = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            data = part.get_payload(decode=True) or b""
            if filename:
                parts[name] = {"filename": filename, "data": data}
            else:
                parts[name] = {"value": data.decode("utf-8", errors="replace")}
        return parts


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    ensure_storage_dirs()
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Serving Docx Image Organizer at http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    run(args.host, args.port)


LEGACY_INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Docx 图片整理</title>
  <style>
    :root {
      --bg: #f7f7f4;
      --ink: #202124;
      --muted: #667085;
      --line: #d8d8d2;
      --accent: #1f7a6d;
      --accent-dark: #14584f;
      --warn: #9a4f14;
      --panel: #ffffff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    main {
      width: min(1040px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-end;
      margin-bottom: 24px;
    }
    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.15;
      letter-spacing: 0;
    }
    .sub {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 14px;
    }
    .shell {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 20px;
      align-items: start;
    }
    .settings {
      margin-top: 18px;
      display: grid;
      gap: 14px;
    }
    .setting-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .presets {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .presets button, .segmented button {
      background: #e8ecea;
      color: var(--ink);
      min-height: 34px;
      padding: 0 10px;
    }
    .segmented {
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 7px;
      overflow: hidden;
      background: #e8ecea;
    }
    .segmented button {
      border-radius: 0;
      border-right: 1px solid var(--line);
    }
    .segmented button:last-child {
      border-right: 0;
    }
    .segmented button.active {
      background: var(--accent);
      color: #fff;
    }
    .range-row {
      display: grid;
      grid-template-columns: 44px minmax(140px, 1fr) 70px;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
    }
    input[type="range"] {
      width: 100%;
      accent-color: var(--accent);
    }
    input[type="number"] {
      width: 70px;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 8px;
      font-size: 14px;
    }
    .preview {
      margin-top: 6px;
      border: 1px solid var(--line);
      background: #f4f1ea;
      border-radius: 8px;
      padding: 18px;
    }
    .paper {
      width: min(100%, 420px);
      aspect-ratio: 1 / 1.28;
      margin: 0 auto;
      background: #fff;
      border: 1px solid #d7d9d4;
      box-shadow: 0 8px 24px rgba(32, 33, 36, .08);
      padding: 12% 10%;
    }
    .body-area {
      height: 100%;
      border: 1px dashed #ccd3cf;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding-top: 18%;
    }
    .image-box {
      height: 22%;
      min-height: 36px;
      background: linear-gradient(135deg, #dbe7e2, #8bb7aa);
      border: 1px solid #6f9d91;
      border-radius: 4px;
      transition: width .12s ease;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
    }
    .drop {
      border: 1px dashed #9aa4a0;
      border-radius: 8px;
      min-height: 220px;
      display: grid;
      place-items: center;
      text-align: center;
      padding: 24px;
      background: #fbfbf8;
      transition: border-color .12s ease, background .12s ease;
      cursor: pointer;
    }
    .drop.dragover {
      border-color: var(--accent);
      background: #eef7f4;
    }
    .drop strong {
      display: block;
      font-size: 18px;
      margin-bottom: 8px;
    }
    .drop span {
      color: var(--muted);
      font-size: 14px;
    }
    input[type="file"] { display: none; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }
    button, .button {
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      min-height: 40px;
      padding: 0 14px;
      font-size: 14px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      white-space: nowrap;
    }
    button.secondary, .button.secondary {
      background: #e8ecea;
      color: var(--ink);
    }
    button:disabled { opacity: .55; cursor: wait; }
    button:hover, .button:hover { background: var(--accent-dark); }
    button.secondary:hover, .button.secondary:hover { background: #dbe3df; }
    .filename {
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .stats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .metric {
      border-bottom: 1px solid var(--line);
      padding: 10px 0;
    }
    .metric b {
      display: block;
      font-size: 22px;
      line-height: 1.1;
    }
    .metric span {
      color: var(--muted);
      font-size: 12px;
    }
    .status {
      margin-top: 16px;
      min-height: 24px;
      color: var(--muted);
      font-size: 14px;
    }
    .status.error { color: #b42318; }
    .report {
      margin-top: 16px;
      max-height: 280px;
      overflow: auto;
      background: #202124;
      color: #e7e7df;
      border-radius: 8px;
      padding: 14px;
      font-size: 12px;
      line-height: 1.45;
    }
    @media (max-width: 820px) {
      header, .shell { display: block; }
      .panel + .panel { margin-top: 16px; }
      h1 { font-size: 24px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Docx 图片整理</h1>
        <p class="sub">统一最大宽度、居中、压缩，并保留原始备份。</p>
      </div>
    </header>
    <section class="shell">
      <div class="panel">
        <label class="drop" id="dropZone" for="fileInput">
          <span>
            <strong>选择或拖入 Word 文档</strong>
            <span>拖入 .docx 后会自动解析，最大 50MB</span>
          </span>
        </label>
        <input id="fileInput" type="file" accept=".docx" />
        <div class="filename" id="fileName">尚未选择文件</div>
        <div class="settings" id="settings" hidden>
          <div class="setting-row">
            <strong>图片统一宽度</strong>
            <label>
              <input id="ratioInput" type="number" min="40" max="100" step="1" value="80" />
              %
            </label>
          </div>
          <div class="presets">
            <button type="button" data-ratio="60">紧凑 60%</button>
            <button type="button" data-ratio="80">标准 80%</button>
            <button type="button" data-ratio="100">满宽 100%</button>
          </div>
          <div class="range-row">
            <span>40%</span>
            <input id="ratioRange" type="range" min="40" max="100" step="1" value="80" />
            <span>100%</span>
          </div>
          <div class="setting-row">
            <strong>图片对齐</strong>
            <div class="segmented" role="group" aria-label="图片对齐方式">
              <button type="button" data-align="left">居左</button>
              <button type="button" data-align="center" class="active">居中</button>
              <button type="button" data-align="right">居右</button>
            </div>
          </div>
          <div class="preview">
            <div class="paper" aria-label="模拟 Word 页面">
              <div id="bodyArea" class="body-area">
                <div id="imageBox" class="image-box" style="width: 80%"></div>
              </div>
            </div>
          </div>
        </div>
        <div class="actions">
          <button id="inspectBtn" disabled>解析文档</button>
          <button id="submitBtn" disabled hidden>按此宽度整理图片</button>
          <a id="resultLink" class="button" hidden>下载处理后文档</a>
          <a id="backupLink" class="button secondary" hidden>下载原始备份</a>
        </div>
        <div id="status" class="status"></div>
        <pre id="report" class="report" hidden></pre>
      </div>
      <aside class="panel">
        <div class="stats">
          <div class="metric"><b id="mInline">0</b><span>嵌入图片</span></div>
          <div class="metric"><b id="mScaled">0</b><span>缩小图片</span></div>
          <div class="metric"><b id="mCentered">0</b><span>居中段落</span></div>
          <div class="metric"><b id="mCompressed">0</b><span>压缩图片</span></div>
          <div class="metric"><b id="mSkipped">0</b><span>跳过图片</span></div>
          <div class="metric"><b id="mDelta">0 KB</b><span>文件变化</span></div>
        </div>
      </aside>
    </section>
  </main>
  <script>
    const fileInput = document.getElementById("fileInput");
    const dropZone = document.getElementById("dropZone");
    const fileName = document.getElementById("fileName");
    const inspectBtn = document.getElementById("inspectBtn");
    const submitBtn = document.getElementById("submitBtn");
    const statusEl = document.getElementById("status");
    const reportEl = document.getElementById("report");
    const resultLink = document.getElementById("resultLink");
    const backupLink = document.getElementById("backupLink");
    const settings = document.getElementById("settings");
    const ratioRange = document.getElementById("ratioRange");
    const ratioInput = document.getElementById("ratioInput");
    const imageBox = document.getElementById("imageBox");
    const bodyArea = document.getElementById("bodyArea");
    let selectedAlignment = "center";
    let inspectedJobId = null;

    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      setSelectedFile(file, false);
    });

    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        dropZone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        dropZone.classList.remove("dragover");
      });
    });

    dropZone.addEventListener("drop", (event) => {
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(".docx")) {
        statusEl.textContent = "请拖入 .docx 文件";
        statusEl.className = "status error";
        return;
      }
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files;
      setSelectedFile(file, true);
    });

    inspectBtn.addEventListener("click", async () => {
      const file = fileInput.files[0];
      if (!file) return;
      await inspectSelectedFile(file);
    });

    async function inspectSelectedFile(file) {
      inspectBtn.disabled = true;
      statusEl.textContent = "正在解析...";
      statusEl.className = "status";
      const form = new FormData();
      form.append("file", file);
      try {
        const response = await fetch("/api/docx/inspect", { method: "POST", body: form });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "解析失败");
        inspectedJobId = payload.job_id;
        renderInspect(payload);
        statusEl.textContent = "解析完成，请设置图片宽度后应用整理";
      } catch (error) {
        statusEl.textContent = error.message;
        statusEl.className = "status error";
      } finally {
        inspectBtn.disabled = false;
      }
    }

    function setSelectedFile(file, autoInspect) {
      fileName.textContent = file ? file.name : "尚未选择文件";
      inspectBtn.disabled = !file;
      submitBtn.disabled = true;
      submitBtn.hidden = true;
      inspectedJobId = null;
      settings.hidden = true;
      statusEl.textContent = "";
      statusEl.className = "status";
      reportEl.hidden = true;
      resultLink.hidden = true;
      backupLink.hidden = true;
      if (file && autoInspect) {
        inspectSelectedFile(file);
      }
    }

    submitBtn.addEventListener("click", async () => {
      if (!inspectedJobId) return;
      submitBtn.disabled = true;
      statusEl.textContent = "正在处理...";
      statusEl.className = "status";
      const form = new FormData();
      form.append("job_id", inspectedJobId);
      form.append("max_width_ratio", String(Number(ratioInput.value) / 100));
      form.append("alignment", selectedAlignment);
      try {
        const response = await fetch("/api/docx/format", { method: "POST", body: form });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "处理失败");
        renderReport(payload);
        statusEl.textContent = "处理完成";
      } catch (error) {
        statusEl.textContent = error.message;
        statusEl.className = "status error";
      } finally {
        submitBtn.disabled = false;
      }
    });

    ratioRange.addEventListener("input", () => setRatio(ratioRange.value));
    ratioInput.addEventListener("input", () => setRatio(ratioInput.value));
    document.querySelectorAll("[data-ratio]").forEach((button) => {
      button.addEventListener("click", () => setRatio(button.dataset.ratio));
    });
    document.querySelectorAll("[data-align]").forEach((button) => {
      button.addEventListener("click", () => setAlignment(button.dataset.align));
    });

    function setRatio(rawValue) {
      const value = Math.min(100, Math.max(40, Number(rawValue) || 80));
      ratioRange.value = value;
      ratioInput.value = value;
      imageBox.style.width = `${value}%`;
    }

    function setAlignment(value) {
      selectedAlignment = value;
      document.querySelectorAll("[data-align]").forEach((button) => {
        button.classList.toggle("active", button.dataset.align === value);
      });
      const map = { left: "flex-start", center: "center", right: "flex-end" };
      bodyArea.style.justifyContent = map[value] || "center";
    }

    function renderInspect(payload) {
      const doc = payload.document || {};
      settings.hidden = false;
      submitBtn.hidden = false;
      submitBtn.disabled = false;
      resultLink.hidden = true;
      backupLink.href = `/api/docx/backup/${payload.job_id}`;
      backupLink.hidden = false;
      document.getElementById("mInline").textContent = doc.inline_images ?? 0;
      document.getElementById("mScaled").textContent = 0;
      document.getElementById("mCentered").textContent = 0;
      document.getElementById("mCompressed").textContent = 0;
      document.getElementById("mSkipped").textContent = 0;
      document.getElementById("mDelta").textContent = formatBytes(0);
      reportEl.textContent = JSON.stringify(payload, null, 2);
      reportEl.hidden = false;
      setRatio((payload.settings?.default_width_ratio || 0.8) * 100);
      setAlignment(payload.settings?.default_alignment || "center");
    }

    function renderReport(payload) {
      const stats = payload.stats || {};
      document.getElementById("mInline").textContent = stats.inline_images ?? 0;
      document.getElementById("mScaled").textContent = stats.resized_images ?? stats.scaled_images ?? 0;
      document.getElementById("mCentered").textContent = stats.centered_paragraphs ?? 0;
      document.getElementById("mCompressed").textContent = stats.compressed_images ?? 0;
      document.getElementById("mSkipped").textContent = stats.skipped_images ?? 0;
      document.getElementById("mDelta").textContent = formatBytes(stats.file_size_delta || 0);
      resultLink.href = payload.result_download_url;
      backupLink.href = payload.backup_download_url;
      resultLink.hidden = false;
      backupLink.hidden = false;
      reportEl.textContent = JSON.stringify(payload, null, 2);
      reportEl.hidden = false;
    }

    function formatBytes(value) {
      const sign = value > 0 ? "+" : value < 0 ? "-" : "";
      const kb = Math.abs(value) / 1024;
      return `${sign}${kb.toFixed(1)} KB`;
    }
  </script>
</body>
</html>
"""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Word / WPS 图片整理助手</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --ink: #18212f;
      --muted: #667085;
      --line: #d9e0ea;
      --line-strong: #c8d2df;
      --accent: #2563eb;
      --accent-dark: #1d4ed8;
      --accent-soft: #e8f0ff;
      --success: #12805c;
      --danger: #b42318;
      --shadow: 0 18px 45px rgba(31, 41, 55, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(180deg, #eef4ff 0, rgba(238, 244, 255, 0) 240px),
        var(--bg);
    }
    main {
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-end;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    .sub {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 14px;
    }
    .pill {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, .72);
      border-radius: 999px;
      color: var(--muted);
      font-size: 13px;
      padding: 8px 12px;
      white-space: nowrap;
    }
    .shell {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 380px;
      gap: 18px;
      align-items: start;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .work-panel {
      padding: 18px;
      display: grid;
      gap: 16px;
    }
    .side-panel {
      position: sticky;
      top: 18px;
      overflow: hidden;
    }
    .section-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    h2 {
      margin: 0;
      font-size: 16px;
      line-height: 1.3;
      letter-spacing: 0;
    }
    .drop {
      border: 1px dashed var(--line-strong);
      border-radius: 8px;
      min-height: 174px;
      display: grid;
      place-items: center;
      text-align: center;
      padding: 24px;
      background: var(--panel-soft);
      transition: border-color .14s ease, background .14s ease, transform .14s ease;
      cursor: pointer;
    }
    .drop:hover,
    .drop.dragover {
      border-color: var(--accent);
      background: var(--accent-soft);
      transform: translateY(-1px);
    }
    .drop strong {
      display: block;
      font-size: 18px;
      margin-bottom: 8px;
    }
    .drop span {
      color: var(--muted);
      font-size: 14px;
    }
    input[type="file"] { display: none; }
    .file-card {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 46px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fff;
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }
    .filename {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .settings {
      border-top: 1px solid var(--line);
      padding-top: 16px;
      display: grid;
      gap: 16px;
    }
    .setting-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .field-title {
      font-weight: 650;
      font-size: 14px;
    }
    .field-hint {
      color: var(--muted);
      font-size: 12px;
      margin-top: 3px;
    }
    .presets {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .range-row {
      display: grid;
      grid-template-columns: minmax(160px, 1fr) auto;
      gap: 12px;
      align-items: center;
    }
    input[type="range"] {
      width: 100%;
      accent-color: var(--accent);
    }
    input[type="number"] {
      width: 76px;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 0 8px;
      font-size: 14px;
      color: var(--ink);
      background: #fff;
    }
    .segmented {
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #eef2f7;
    }
    button, .button {
      border: 0;
      border-radius: 7px;
      background: var(--accent);
      color: #fff;
      min-height: 40px;
      padding: 0 14px;
      font-size: 14px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      white-space: nowrap;
    }
    button.secondary, .button.secondary,
    .presets button, .segmented button {
      background: #eef2f7;
      color: var(--ink);
    }
    .segmented button {
      border-radius: 0;
      border-right: 1px solid var(--line);
      min-height: 36px;
    }
    .segmented button:last-child { border-right: 0; }
    .segmented button.active,
    .presets button.active {
      background: var(--accent);
      color: #fff;
    }
    button:disabled {
      opacity: .56;
      cursor: not-allowed;
    }
    button:hover:not(:disabled), .button:hover { background: var(--accent-dark); }
    button.secondary:hover:not(:disabled), .button.secondary:hover { background: #e2e8f0; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .primary-action {
      min-width: 150px;
      font-weight: 650;
    }
    .status {
      min-height: 24px;
      color: var(--muted);
      font-size: 14px;
      margin-top: 10px;
    }
    .status.ready { color: var(--success); }
    .status.error { color: var(--danger); }
    .preview-head {
      padding: 16px 16px 0;
    }
    .preview {
      padding: 16px;
      background: linear-gradient(180deg, #f8fbff 0, #eef3f9 100%);
    }
    .paper {
      width: min(100%, 330px);
      aspect-ratio: 1 / 1.3;
      margin: 0 auto;
      background: #fff;
      border: 1px solid #d5dce7;
      box-shadow: 0 14px 30px rgba(31, 41, 55, .12);
      padding: 13% 10%;
    }
    .paper-line {
      height: 6px;
      border-radius: 999px;
      background: #e8edf5;
      margin-bottom: 8px;
    }
    .paper-line.short { width: 64%; }
    .body-area {
      height: 62%;
      margin-top: 14%;
      display: flex;
      align-items: flex-start;
      justify-content: center;
    }
    .image-box {
      height: 44%;
      min-height: 46px;
      background:
        linear-gradient(135deg, rgba(37, 99, 235, .16), rgba(18, 128, 92, .22)),
        #dce8ff;
      border: 1px solid #96aeda;
      border-radius: 6px;
      transition: width .12s ease;
      position: relative;
    }
    .image-box::after {
      content: "";
      position: absolute;
      inset: 10px;
      border: 1px dashed rgba(37, 99, 235, .38);
      border-radius: 4px;
    }
    .summary {
      padding: 16px;
      border-top: 1px solid var(--line);
      display: grid;
      gap: 12px;
    }
    .stats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
    }
    .metric b {
      display: block;
      font-size: 22px;
      line-height: 1.05;
    }
    .metric span {
      color: var(--muted);
      font-size: 12px;
    }
    details {
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
    }
    .report {
      margin: 12px 0 0;
      max-height: 220px;
      overflow: auto;
      background: #111827;
      color: #e5e7eb;
      border-radius: 8px;
      padding: 12px;
      font-size: 12px;
      line-height: 1.45;
    }
    [hidden] { display: none !important; }
    @media (max-width: 900px) {
      header { display: block; }
      .pill { display: inline-flex; margin-top: 12px; }
      .shell { grid-template-columns: 1fr; }
      .side-panel { position: static; }
    }
    @media (max-width: 560px) {
      main { width: min(100% - 20px, 1180px); padding-top: 18px; }
      h1 { font-size: 24px; }
      .work-panel, .preview, .summary { padding: 14px; }
      .range-row { grid-template-columns: 1fr; }
      .stats { grid-template-columns: 1fr; }
      .actions > * { width: 100%; }
      .segmented { width: 100%; }
      .segmented button { flex: 1; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Word / WPS 图片整理助手</h1>
        <p class="sub">上传 Word/WPS 导出的 .docx，统一图片宽度和对齐方式，处理完成后直接下载新文档。</p>
      </div>
      <div class="pill">本地处理 · 保留原始备份</div>
    </header>

    <section class="shell">
      <div class="panel work-panel">
        <section>
          <div class="section-title">
            <h2>1. 选择文档</h2>
          </div>
          <label class="drop" id="dropZone" for="fileInput">
            <span>
              <strong>点击选择，或把 Word / WPS 文档拖到这里</strong>
              <span>支持 Word/WPS .docx，最大 50MB。拖入后会自动分析。</span>
            </span>
          </label>
          <input id="fileInput" type="file" accept=".docx,.pdf" />
          <div class="file-card">
            <span class="filename" id="fileName">尚未选择文件</span>
            <span id="fileSize">-</span>
          </div>
          <div class="setting-row" style="margin-top: 14px;" hidden>
            <div>
              <div class="field-title">飞书在线文档</div>
              <div class="field-hint" id="feishuHint">正在检测飞书导入状态...</div>
            </div>
          </div>
          <div class="actions" hidden>
            <input id="feishuUrl" type="url" placeholder="https://xxx.feishu.cn/docx/..." style="flex:1; min-width:220px; min-height:40px; border:1px solid var(--line); border-radius:6px; padding:0 10px;" />
            <button id="feishuBtn" class="secondary" type="button">导入飞书文档</button>
          </div>
        </section>

        <section class="settings" id="settings" hidden>
          <div class="section-title">
            <h2>2. 设置图片样式</h2>
          </div>
          <div class="setting-row">
            <div>
              <div class="field-title">统一图片宽度</div>
              <div class="field-hint">右侧预览会同步显示效果</div>
            </div>
            <label>
              <input id="ratioInput" type="number" min="40" max="100" step="1" value="80" />
              %
            </label>
          </div>
          <div class="range-row">
            <input id="ratioRange" type="range" min="40" max="100" step="1" value="80" aria-label="图片宽度" />
            <div class="presets">
              <button type="button" data-ratio="60">紧凑</button>
              <button type="button" data-ratio="80" class="active">标准</button>
              <button type="button" data-ratio="100">满宽</button>
            </div>
          </div>
          <div class="setting-row">
            <div>
              <div class="field-title">图片对齐</div>
              <div class="field-hint">通常居中最适合报告和论文</div>
            </div>
            <div class="segmented" role="group" aria-label="图片对齐方式">
              <button type="button" data-align="left">左</button>
              <button type="button" data-align="center" class="active">居中</button>
              <button type="button" data-align="right">右</button>
            </div>
          </div>
        </section>

        <section>
          <div class="section-title">
            <h2>3. 处理并下载</h2>
          </div>
          <div class="actions">
            <button id="inspectBtn" class="secondary" disabled>分析文档</button>
            <button id="submitBtn" class="primary-action" disabled hidden>整理图片</button>
            <a id="resultLink" class="button" hidden>下载新文档</a>
            <a id="backupLink" class="button secondary" hidden>下载原文件</a>
          </div>
          <div id="status" class="status">先选择一个 Word / WPS 文档开始。</div>
        </section>
      </div>

      <aside class="panel side-panel">
        <div class="preview-head">
          <div class="section-title">
            <h2>图片预览</h2>
            <span class="pill" id="previewBadge">80% · 居中</span>
          </div>
        </div>
        <div class="preview">
          <div class="paper" aria-label="模拟 Word 页面">
            <div class="paper-line"></div>
            <div class="paper-line short"></div>
            <div id="bodyArea" class="body-area">
              <div id="imageBox" class="image-box" style="width: 80%"></div>
            </div>
            <div class="paper-line"></div>
            <div class="paper-line short"></div>
          </div>
        </div>
        <div class="summary">
          <div class="section-title">
            <h2>处理摘要</h2>
          </div>
          <div class="stats">
            <div class="metric"><b id="mInline">0</b><span>文档图片</span></div>
            <div class="metric"><b id="mScaled">0</b><span>调整宽度</span></div>
            <div class="metric"><b id="mCentered">0</b><span>调整对齐</span></div>
            <div class="metric"><b id="mDelta">0 KB</b><span>文件变化</span></div>
          </div>
          <details id="reportDetails" hidden>
            <summary>查看详细报告</summary>
            <pre id="report" class="report"></pre>
          </details>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const fileInput = document.getElementById("fileInput");
    const dropZone = document.getElementById("dropZone");
    const fileName = document.getElementById("fileName");
    const fileSize = document.getElementById("fileSize");
    const inspectBtn = document.getElementById("inspectBtn");
    const submitBtn = document.getElementById("submitBtn");
    const feishuUrl = document.getElementById("feishuUrl");
    const feishuBtn = document.getElementById("feishuBtn");
    const statusEl = document.getElementById("status");
    const reportEl = document.getElementById("report");
    const reportDetails = document.getElementById("reportDetails");
    const resultLink = document.getElementById("resultLink");
    const backupLink = document.getElementById("backupLink");
    const settings = document.getElementById("settings");
    const ratioRange = document.getElementById("ratioRange");
    const ratioInput = document.getElementById("ratioInput");
    const imageBox = document.getElementById("imageBox");
    const bodyArea = document.getElementById("bodyArea");
    const previewBadge = document.getElementById("previewBadge");
    let selectedAlignment = "center";
    let inspectedJobId = null;
    let selectedFileType = "docx";

    // Feishu import is intentionally hidden until user-level OAuth is designed.

    fileInput.addEventListener("change", () => {
      setSelectedFile(fileInput.files[0], false);
    });

    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        dropZone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        dropZone.classList.remove("dragover");
      });
    });

    dropZone.addEventListener("drop", (event) => {
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) return;
      if (!isSupportedFile(file)) {
        setStatus("请拖入 .docx 或 .pdf 文件。", "error");
        return;
      }
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files;
      setSelectedFile(file, true);
    });

    inspectBtn.addEventListener("click", async () => {
      const file = fileInput.files[0];
      if (file) await inspectSelectedFile(file);
    });

    feishuBtn.addEventListener("click", async () => {
      const link = feishuUrl.value.trim();
      if (!link) {
        setStatus("请先输入飞书文档链接。", "error");
        return;
      }
      feishuBtn.disabled = true;
      selectedFileType = "docx";
      setStatus("正在从飞书导出文档，请稍等...", "");
      const form = new FormData();
      form.append("url", link);
      try {
        const response = await fetch("/api/feishu/import", { method: "POST", body: form });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "飞书文档导入失败");
        inspectedJobId = payload.job_id;
        fileName.textContent = payload.original_filename || "飞书导出的文档.docx";
        fileSize.textContent = formatFileSize(payload.document?.file_size || 0);
        inspectBtn.disabled = true;
        renderInspect(payload);
        setStatus("飞书文档已导入。确认右侧预览后，点击“整理图片”。", "ready");
      } catch (error) {
        setStatus(error.message, "error");
      } finally {
        feishuBtn.disabled = false;
      }
    });

    async function checkFeishuStatus() {
      const hint = document.getElementById("feishuHint");
      try {
        const response = await fetch("/api/feishu/status");
        const payload = await response.json();
        hint.textContent = payload.message || "飞书导入状态未知。";
        feishuBtn.disabled = !payload.available;
        feishuUrl.disabled = !payload.available;
        if (!payload.available) {
          feishuUrl.placeholder = "当前未启用：请先从飞书下载 Word 后上传";
        }
      } catch {
        hint.textContent = "无法检测飞书导入状态。请先从飞书下载 Word 后上传。";
        feishuBtn.disabled = true;
        feishuUrl.disabled = true;
      }
    }

    submitBtn.addEventListener("click", async () => {
      if (!inspectedJobId) return;
      submitBtn.disabled = true;
      setStatus(selectedFileType === "pdf" ? "正在压缩 PDF 图片，请稍等..." : "正在整理图片，请稍等...", "");
      const form = new FormData();
      form.append("job_id", inspectedJobId);
      if (selectedFileType === "docx") {
        form.append("max_width_ratio", String(Number(ratioInput.value) / 100));
        form.append("alignment", selectedAlignment);
      }
      try {
        const response = await fetch(selectedFileType === "pdf" ? "/api/pdf/format" : "/api/docx/format", { method: "POST", body: form });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "处理失败");
        renderReport(payload);
        setStatus(selectedFileType === "pdf" ? "PDF 压缩完成，可以下载新文件了。" : "处理完成，可以下载新文档了。", "ready");
      } catch (error) {
        setStatus(error.message, "error");
      } finally {
        submitBtn.disabled = false;
      }
    });

    ratioRange.addEventListener("input", () => setRatio(ratioRange.value));
    ratioInput.addEventListener("input", () => setRatio(ratioInput.value));
    document.querySelectorAll("[data-ratio]").forEach((button) => {
      button.addEventListener("click", () => setRatio(button.dataset.ratio));
    });
    document.querySelectorAll("[data-align]").forEach((button) => {
      button.addEventListener("click", () => setAlignment(button.dataset.align));
    });

    async function inspectSelectedFile(file) {
      inspectBtn.disabled = true;
      setStatus("正在分析文档...", "");
      const form = new FormData();
      form.append("file", file);
      try {
        const response = await fetch(selectedFileType === "pdf" ? "/api/pdf/inspect" : "/api/docx/inspect", { method: "POST", body: form });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "分析失败");
        inspectedJobId = payload.job_id;
        renderInspect(payload);
        setStatus(selectedFileType === "pdf" ? "分析完成。PDF 当前只支持图片压缩，不修改版式。" : "分析完成。确认右侧预览后，点击“整理图片”。", "ready");
      } catch (error) {
        setStatus(error.message, "error");
      } finally {
        inspectBtn.disabled = false;
      }
    }

    function setSelectedFile(file, autoInspect) {
      fileName.textContent = file ? file.name : "尚未选择文件";
      fileSize.textContent = file ? formatFileSize(file.size) : "-";
      selectedFileType = file && file.name.toLowerCase().endsWith(".pdf") ? "pdf" : "docx";
      inspectBtn.disabled = !file;
      submitBtn.disabled = true;
      submitBtn.hidden = true;
      inspectedJobId = null;
      settings.hidden = true;
      reportDetails.hidden = true;
      reportDetails.open = false;
      reportEl.textContent = "";
      resultLink.hidden = true;
      backupLink.hidden = true;
      resetMetrics();
      inspectBtn.textContent = selectedFileType === "pdf" ? "分析 PDF" : "分析文档";
      submitBtn.textContent = selectedFileType === "pdf" ? "压缩 PDF 图片" : "整理图片";
      setStatus(file ? "已选择文件，点击“分析”继续。" : "先选择一个 Word / WPS .docx 或 PDF 文件开始。", "");
      if (file && autoInspect) inspectSelectedFile(file);
    }

    function setRatio(rawValue) {
      const value = Math.min(100, Math.max(40, Number(rawValue) || 80));
      ratioRange.value = value;
      ratioInput.value = value;
      imageBox.style.width = `${value}%`;
      document.querySelectorAll("[data-ratio]").forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.ratio) === value);
      });
      updatePreviewBadge();
    }

    function setAlignment(value) {
      selectedAlignment = value;
      document.querySelectorAll("[data-align]").forEach((button) => {
        button.classList.toggle("active", button.dataset.align === value);
      });
      const map = { left: "flex-start", center: "center", right: "flex-end" };
      bodyArea.style.justifyContent = map[value] || "center";
      updatePreviewBadge();
    }

    function renderInspect(payload) {
      const doc = payload.document || {};
      settings.hidden = selectedFileType === "pdf";
      submitBtn.hidden = false;
      submitBtn.disabled = false;
      resultLink.hidden = true;
      backupLink.href = selectedFileType === "pdf" ? `/api/pdf/backup/${payload.job_id}` : `/api/docx/backup/${payload.job_id}`;
      backupLink.hidden = false;
      document.getElementById("mInline").textContent = doc.inline_images ?? doc.images ?? 0;
      document.getElementById("mScaled").textContent = 0;
      document.getElementById("mCentered").textContent = 0;
      document.getElementById("mDelta").textContent = formatBytes(0);
      setReport(payload);
      if (selectedFileType === "docx") {
        setRatio((payload.settings?.default_width_ratio || 0.8) * 100);
        setAlignment(payload.settings?.default_alignment || "center");
      }
    }

    function renderReport(payload) {
      const stats = payload.stats || {};
      document.getElementById("mInline").textContent = stats.inline_images ?? stats.images ?? 0;
      document.getElementById("mScaled").textContent = stats.resized_images ?? stats.scaled_images ?? 0;
      document.getElementById("mCentered").textContent = stats.centered_paragraphs ?? 0;
      document.getElementById("mDelta").textContent = formatBytes(stats.file_size_delta || 0);
      resultLink.href = payload.result_download_url;
      backupLink.href = payload.backup_download_url;
      resultLink.hidden = false;
      backupLink.hidden = false;
      setReport(payload);
    }

    function resetMetrics() {
      document.getElementById("mInline").textContent = 0;
      document.getElementById("mScaled").textContent = 0;
      document.getElementById("mCentered").textContent = 0;
      document.getElementById("mDelta").textContent = formatBytes(0);
    }

    function setReport(payload) {
      reportEl.textContent = JSON.stringify(payload, null, 2);
      reportDetails.hidden = false;
    }

    function setStatus(message, type) {
      statusEl.textContent = message;
      statusEl.className = `status ${type || ""}`.trim();
    }

    function updatePreviewBadge() {
      const alignText = { left: "左对齐", center: "居中", right: "右对齐" }[selectedAlignment] || "居中";
      previewBadge.textContent = `${ratioInput.value}% · ${alignText}`;
    }

    function formatBytes(value) {
      const sign = value > 0 ? "+" : value < 0 ? "-" : "";
      const kb = Math.abs(value) / 1024;
      return `${sign}${kb.toFixed(1)} KB`;
    }

    function formatFileSize(value) {
      if (!Number.isFinite(value)) return "-";
      if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
      return `${(value / 1024 / 1024).toFixed(1)} MB`;
    }

    function isSupportedFile(file) {
      const name = file.name.toLowerCase();
      return name.endsWith(".docx") || name.endsWith(".pdf");
    }
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
