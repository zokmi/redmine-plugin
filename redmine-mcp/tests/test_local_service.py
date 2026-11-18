import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


@pytest.mark.parametrize("interruption", ["worker_crash", "truncated_request"])
def test_node_serves_requests_after_interruption(tmp_path: Path, interruption: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    package = tmp_path / "redmine_mcp"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "local_worker.py").write_text(
        """
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

def watch_parent():
    os.read(sys.stdin.fileno(), 1)
    os._exit(0)

Thread(target=watch_parent, daemon=True).start()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("X-Crash"):
            os._exit(17)
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.headers.get("X-Truncated"):
            self.rfile.read(1)
            Path("truncated-started").touch()
            self.rfile.read(int(self.headers["Content-Length"]) - 1)
            return
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if message["method"] != "initialize":
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {},
                "serverInfo": {"name": "fixture-worker", "version": "1"},
            },
        }).encode())

server = HTTPServer(("127.0.0.1", 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
""",
        encoding="utf-8",
    )
    token = "x" * 32
    script = Path(__file__).resolve().parents[2] / "scripts" / "local-service.mjs"
    with subprocess.Popen(
        [node, str(script), sys.executable, str(port)],
        cwd=tmp_path,
        env={**os.environ, "REDMINE_MCP_LOCAL_TOKEN": token, "PYTHONPATH": str(tmp_path)},
    ) as service, httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,
        timeout=1,
    ) as http:
        try:
            def wait_for_worker() -> None:
                for _ in range(100):
                    assert service.poll() is None, "Node.js exited with its worker"
                    try:
                        if http.get("/mcp").status_code == 200:
                            return
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                pytest.fail("Worker did not become ready")

            wait_for_worker()
            if interruption == "worker_crash":
                assert http.get("/mcp", headers={"X-Crash": "1"}).status_code == 502
                wait_for_worker()
            else:
                with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                    client.sendall(
                        (
                            f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                            f"Authorization: Bearer {token}\r\n"
                            "Content-Length: 20\r\nX-Truncated: 1\r\n\r\n{"
                        ).encode()
                    )
                    for _ in range(100):
                        if (tmp_path / "truncated-started").exists():
                            break
                        time.sleep(0.02)
                    else:
                        pytest.fail("Worker did not receive the partial request")

                initialized = http.post("/mcp", json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                })
                assert initialized.status_code == 200
                assert initialized.json()["result"]["serverInfo"]["name"] == "fixture-worker"
                assert service.poll() is None
        finally:
            # The fixture worker exits when its parent closes stdin.
            service.terminate()


@pytest.mark.parametrize("runtime", ["python", "node"])
async def test_parallel_sessions_share_one_service(tmp_path: Path, runtime: str) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = tmp_path / "config.toml"
    config.write_text(
        '[sites.test]\nurl = "https://redmine.example.test"\napi_key = "test"\n',
        encoding="utf-8",
    )
    token = "test_local_token_only_" + "x" * 32
    env = {
        **os.environ,
        "REDMINE_CONFIG": str(config),
        "REDMINE_MCP_LOCAL_TOKEN": token,
        "LOCALAPPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "PYTHONUTF8": "1",
    }
    command = [sys.executable, "-m", "redmine_mcp", "serve", "--port", str(port)]
    if runtime == "node":
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node.js is unavailable")
        script = Path(__file__).resolve().parents[2] / "scripts" / "local-service.mjs"
        command = [node, str(script), sys.executable, str(port)]
    url = f"http://127.0.0.1:{port}/mcp"
    headers = {"Authorization": f"Bearer {token}"}
    ready = asyncio.Event()
    session_count = 0

    async def open_session() -> None:
        nonlocal session_count
        async with (
            httpx2.AsyncClient(headers=headers, trust_env=False) as http,
            streamable_http_client(url, http_client=http) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            assert "list_sites" in {tool.name for tool in (await session.list_tools()).tools}
            result = await session.call_tool("list_sites")
            assert not result.is_error
            session_count += 1
            if session_count == 4:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=10)

    with (tmp_path / "service.log").open("w+b") as log:
        service = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1) as http:
                for _ in range(150):
                    assert service.poll() is None
                    try:
                        response = await http.get(url)
                    except httpx.TransportError:
                        await asyncio.sleep(0.1)
                        continue
                    if response.status_code == 503:
                        await asyncio.sleep(0.1)
                        continue
                    assert response.status_code == 401
                    break
                else:
                    pytest.fail("共用 MCP 服務未開始監聽")

                denied = await http.get(url, headers={**headers, "Origin": "https://evil.test"})
                assert denied.status_code == 403

            duplicate = await asyncio.to_thread(
                subprocess.run, command, env=env, capture_output=True, timeout=15, check=False,
            )
            assert duplicate.returncode != 0
            assert "無法監聽" in duplicate.stderr.decode()
            assert b"session manager started" not in duplicate.stderr

            await asyncio.wait_for(
                asyncio.gather(*(open_session() for _ in range(4))), timeout=20,
            )
            assert service.poll() is None
        finally:
            if service.poll() is None:
                service.terminate()
            service.wait(timeout=10)
            log.seek(0)
            print(log.read().decode(errors="replace"))
