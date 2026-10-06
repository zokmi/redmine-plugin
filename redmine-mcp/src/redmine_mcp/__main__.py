"""Redmine MCP server 的進入點。"""
from __future__ import annotations

import asyncio
import logging
import sys

from redmine_mcp.config import ConfigError, load_settings
from redmine_mcp.config_file import load_config_file, resolve_config_path
from redmine_mcp.server import create_server
from redmine_mcp.sites import SiteRegistry

#: server 啟動成功時的 log 訊息樣板。抽成模組層級常數是為了讓
#: setup/external.py 的 _STARTED_MARKER 能被測試釘住——冒煙測試靠比對這行文字
#: 判定 server 是否起得來，改掉措辭而沒同步 marker 會讓判定靜默退化成一律失敗。
STARTED_MESSAGE = "Redmine MCP server 啟動，站台：%s"


def main() -> None:
    """載入設定、建立 server 並以 stdio 傳輸執行。

    所有訊息一律輸出到 stderr；stdout 是 MCP 協定通道，寫入會破壞協定。
    """
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")

    # 先印出實際使用的參數檔路徑：排錯時第一步就是確認讀到的是哪個檔。
    config_path = resolve_config_path()
    if config_path.is_file():
        logging.info("讀取參數檔：%s", config_path)
    else:
        logging.info("未找到參數檔 %s，改由環境變數提供設定", config_path)

    try:
        settings = load_settings(load_config_file(config_path))
    except ConfigError as exc:
        # 一併印出參數檔的預期位置：以套件安裝時使用者手邊沒有 config.example.toml，
        # 光說「請填 config.toml」無從得知該把檔案放到哪裡。
        print(f"設定錯誤：{exc}", file=sys.stderr)
        print(f"參數檔預期位置：{config_path}", file=sys.stderr)
        raise SystemExit(1) from exc

    sites = SiteRegistry(settings)
    mcp = create_server(sites)
    logging.info(STARTED_MESSAGE, "、".join(sites.names))
    try:
        mcp.run()
    finally:
        # mcp.run() 自行管理事件迴圈且會在返回時關閉它，因此收尾要另起一個迴圈。
        # 這裡的 sites.aclose() 會連帶關閉 PyCurlTransport 的 handle 池與 httpx client 資源；
        # 即使關閉失敗也不該影響行程結束，故只記錄不往外拋。
        try:
            asyncio.run(sites.aclose())
        except Exception:
            logging.debug("關閉站台 client 時發生錯誤，已忽略", exc_info=True)


def entrypoint() -> None:
    """console script 的進入點。

    無參數時直接啟動 server，**刻意不 import typer**：MCP client 每次啟動 session
    都會開一次本執行檔，而 typer 會連帶載入 shellingham 等與 server 無關的套件。
    SETUP.md 記錄 client 端連線 timeout 只有 30 秒、冷路徑曾實測 37% 斷線率，這條
    路徑沒有理由去付與它無關的 import 成本。（rich 與 click 例外：MCP SDK 自己就會
    載入它們，省不掉，因此精靈用 rich 輸出並不增加啟動負擔。）

    有參數時才載入 Typer app，由它處理子命令、--help 與參數錯誤。
    """
    if not sys.argv[1:]:
        main()
        return

    from redmine_mcp.cli import app

    app()


if __name__ == "__main__":
    entrypoint()
