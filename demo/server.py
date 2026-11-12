#!/usr/bin/env python3
"""
校准证书辅助审核 · 批量审核演示（本地实时版）

只做两件事：
  1. 托管本目录下的 index.html；
  2. 提供 /api/batch/* 接口，调用 app/ 下的真实审核引擎。

用法（在项目根目录，也就是同时含有 app/ 和 demo/ 的那一层）：
    python3 demo/server.py                 # 默认 http://127.0.0.1:8765/
    python3 demo/server.py --port 9000

接口：
    POST /api/batch/start                          JSON {"sample": "bulk"} 或 multipart(字段名 files)
    GET  /api/batch/<job>/progress
    GET  /api/batch/<job>/results
    GET  /api/batch/<job>/cert/<file>
    GET  /api/batch/<job>/page/<file>/<n>.png
    POST /api/batch/<job>/review                   JSON {file, finding_ids, status: 确认|驳回|撤销, note}
"""
import argparse
import http.server
import json
import socketserver
import sys
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from urllib.parse import unquote

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import batch_engine  # noqa: E402  引擎从上一级的 app/ 导入（路径在 batch_engine 里按相对位置解析）

MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def parse_multipart_files(content_type: str, body: bytes, field: str = "files"):
    """解析 multipart/form-data，返回 [(文件名, 字节)]。（标准库的 cgi 已被移除，这里用 email 解析器。）"""
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.iter_parts():
        if part.get_param("name", header="content-disposition") != field:
            continue
        name = part.get_filename()
        if name:
            out.append((name, part.get_payload(decode=True) or b""))
    return out


class DemoHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def _send_json(self, payload: dict, status: int = 200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _parts(self):
        return [unquote(p) for p in self.path.split("?")[0].split("/")[3:]]

    def _batch_get(self):
        # /api/batch/<job>/progress | results | cert/<file> | page/<file>/<n>.png
        parts = self._parts()
        job = batch_engine.JOBS.get(parts[0]) if parts else None
        if not job or len(parts) < 2:
            return self._send_json({"error": "任务不存在"}, status=404)
        if parts[1] == "progress":
            return self._send_json(job.progress())
        if parts[1] == "results":
            if not job.finished:
                return self._send_json({"error": "还没跑完", **job.progress()}, status=409)
            return self._send_json(job.results())
        if parts[1] == "cert" and len(parts) == 3:
            d = job.cert(parts[2])
            return self._send_json(d) if d else self._send_json({"error": "证书不存在"}, status=404)
        if parts[1] == "page" and len(parts) == 4 and parts[3].endswith(".png"):
            try:
                png = job.page_png(parts[2], int(parts[3][:-4]))
            except ValueError:
                png = None
            if not png:
                return self._send_json({"error": "页不存在"}, status=404)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        return self._send_json({"error": "Not found"}, status=404)

    def _batch_start(self):
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            if ctype.startswith("application/json"):
                body = json.loads(self.rfile.read(length) or b"{}")
                job = batch_engine.start_sample(body.get("sample", "bulk"))
            elif ctype.startswith("multipart/form-data"):
                if length > MAX_UPLOAD_BYTES:
                    return self._send_json({"error": "上传内容太大（上限 512MB）"}, status=413)
                files = [(n, d) for n, d in parse_multipart_files(ctype, self.rfile.read(length))
                         if n.lower().endswith(".pdf")]
                if not files:
                    return self._send_json({"error": "没有收到 PDF（字段名 files）"}, status=400)
                job = batch_engine.start_uploaded(files)
            else:
                return self._send_json({"error": "请求格式不对：需要 JSON 或 multipart"}, status=400)
        except FileNotFoundError as e:
            return self._send_json({"error": str(e)}, status=404)
        except ValueError:
            return self._send_json({"error": "请求不是合法 JSON"}, status=400)
        except Exception as e:  # noqa: BLE001
            return self._send_json({"error": f"启动失败：{e}"}, status=500)
        return self._send_json({"job_id": job.id, "total": job.total, "label": job.label})

    def _batch_review(self):
        # POST /api/batch/<job>/review  JSON {file, finding_ids:[...], status: 确认|驳回|撤销, note}
        parts = self._parts()
        job = batch_engine.JOBS.get(parts[0]) if parts else None
        if not job or not job.finished:
            return self._send_json({"error": "任务不存在或还没跑完"}, status=404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send_json({"error": "请求不是 JSON"}, status=400)
        status = body.get("status")
        if status not in ("确认", "驳回", "撤销"):
            return self._send_json({"error": "status 只能是 确认 / 驳回 / 撤销"}, status=400)
        if status == "驳回" and not (body.get("note") or "").strip():
            return self._send_json({"error": "驳回必须写理由"}, status=400)
        d = job.set_review(body.get("file", ""), body.get("finding_ids") or [], status, (body.get("note") or "").strip())
        return self._send_json(d) if d else self._send_json({"error": "证书不存在"}, status=404)

    def do_GET(self):
        if self.path.startswith("/api/batch/"):
            return self._batch_get()
        if self.path.startswith("/api/"):
            return self._send_json({"error": "Not found"}, status=404)
        if self.path.split("?")[0] not in ("/", "/index.html", "/tailwind.css"):  # 只托管页面和样式，不暴露目录里的源码
            return self._send_json({"error": "Not found"}, status=404)
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/batch/start":
            return self._batch_start()
        if path.startswith("/api/batch/") and path.endswith("/review"):
            return self._batch_review()
        self._send_json({"error": "Not found"}, status=404)

    def log_message(self, fmt, *args):  # 进度轮询很密，静音常规访问日志
        if "/api/batch/" not in (self.path or ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="校准证书辅助审核 · 批量审核演示（本地实时版）")
    ap.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    ap.add_argument("--host", type=str, default="127.0.0.1", help="监听地址（默认 127.0.0.1，只本机可访问）")
    args = ap.parse_args()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    # 多线程：批量任务跑的时候，前端还能轮询进度
    with socketserver.ThreadingTCPServer((args.host, args.port), DemoHandler) as httpd:
        print(f"批量审核演示已启动： http://{args.host}:{args.port}/index.html")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            batch_engine.cleanup()
