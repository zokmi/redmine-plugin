import asyncio
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


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
