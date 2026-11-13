"""ComfyUI 多机台队列探测测试（2026-11-09）。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.routers import comfyui
from app.services import comfyui_client


class _QueueHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/queue":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "queue_running": [[0, "p1", {}, {}, {}]],
            "queue_pending": [[1, "p2", {}, {}, {}], [2, "p3", {}, {}, {}]],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QueueHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_queue_summary_reports_running_pending_and_score():
    server, url = _serve()
    try:
        summary = comfyui_client.queue_summary(url)
    finally:
        server.shutdown()
    assert summary["online"] is True
    assert summary["running"] == 1
    assert summary["pending"] == 2
    assert summary["score"] == 4


def test_queue_summary_offline_is_non_blocking():
    summary = comfyui_client.queue_summary("http://127.0.0.1:9", timeout=0.2)
    assert summary["online"] is False
    assert summary["score"] >= 10 ** 5
    assert summary["error"]


def test_probe_hosts_parallel_and_rejects_invalid():
    server, url = _serve()
    try:
        items = comfyui.probe_hosts(comfyui.ProbeHostsRequest(urls=[url, "not a url"]))["items"]
    finally:
        server.shutdown()
    assert len(items) == 2
    good = next(item for item in items if item["url"] == url)
    bad = next(item for item in items if item["url"] == "not a url")
    assert good["online"] is True
    assert good["score"] == 4
    assert bad["online"] is False
    assert bad["error"]
