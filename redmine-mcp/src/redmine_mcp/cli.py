"""命令列介面。

本模組會 import typer，因此**只在有命令列參數時才被載入**——server 的啟動路徑
不經過這裡，詳見 __main__.py 的 entrypoint()。
"""
from __future__ import annotations

import os
import re
from typing import Annotated

import typer

from redmine_mcp.server import LOCAL_PORT

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Redmine MCP server。不帶參數時以 stdio 啟動 server，供 MCP client 使用。"""


@app.command()
def serve(port: Annotated[int, typer.Option(min=1, max=65535)] = LOCAL_PORT) -> None:
    """啟動 Codex 與 Claude 共用的本機 MCP 服務。"""
    from redmine_mcp.__main__ import main

    token = os.environ.get("REDMINE_MCP_LOCAL_TOKEN", "")
    if re.fullmatch(r"[A-Za-z0-9_-]{32,}", token) is None:
        raise typer.BadParameter("REDMINE_MCP_LOCAL_TOKEN 需要至少 32 字元的 URL 安全權杖。")
    main(local_token=token, port=port)


@app.command()
def setup() -> None:
    """互動式設定精靈：建立參數檔並驗證金鑰。MCP 由 plugin 管理。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import wizard
    from redmine_mcp.setup.external import run_command
    from redmine_mcp.setup.verify import verify_credentials

    code = wizard.run_setup(
        wizard.console_prompts(),
        config_path=resolve_config_path(),
        run=run_command,
        verify=verify_credentials,
        finish=False,
    )
    raise typer.Exit(code)


#: config 子群組。多命令群組不需要 setup 那種空 callback——Typer 只在「單一命令且沒有
#: callback」時才會把命令攤平成 app 本身。
config_app = typer.Typer(
    no_args_is_help=True, help="維護參數檔：查看、改站台欄位、刪站台、改全域設定。"
)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """列出參數檔現況（金鑰只顯示末四碼）。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard

    raise typer.Exit(
        config_ops.show(wizard.console_prompts(), config_path=resolve_config_path())
    )


@config_app.command("set-site")
def config_set_site(
    site: str = typer.Argument(..., help="要修改的站台代號"),
    url: str | None = typer.Option(None, "--url", help="新的站台網址（要含子路徑）"),
    description: str | None = typer.Option(None, "--description", help="新的站台說明"),
    clear_description: bool = typer.Option(
        False, "--clear-description", help="移除站台說明那一行"
    ),
    key: bool = typer.Option(
        False, "--key", help="換 API 金鑰；值會以遮蔽的方式詢問，不接受寫在指令裡"
    ),
    yes: bool = typer.Option(False, "--yes", help="跳過寫入前的確認"),
) -> None:
    """修改既有站台的欄位（新增站台請用 redmine-mcp setup）。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard
    from redmine_mcp.setup.external import run_command
    from redmine_mcp.setup.verify import verify_credentials

    raise typer.Exit(
        config_ops.set_site(
            wizard.console_prompts(),
            config_path=resolve_config_path(),
            site=site,
            url=url,
            description=description,
            clear_description=clear_description,
            ask_key=key,
            run=run_command,
            verify=verify_credentials,
            assume_yes=yes,
        )
    )


@config_app.command("remove-site")
def config_remove_site(
    site: str = typer.Argument(..., help="要移除的站台代號"),
    yes: bool = typer.Option(False, "--yes", help="跳過確認（警告仍會印出）"),
) -> None:
    """移除一個站台。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard
    from redmine_mcp.setup.external import run_command

    raise typer.Exit(
        config_ops.remove_site(
            wizard.console_prompts(),
            config_path=resolve_config_path(),
            site=site,
            run=run_command,
            assume_yes=yes,
        )
    )


#: --clear 選項的預設值物件。獨立成模組層級變數是為了避開 ruff B008
#: （list 型別搭配 typer.Option() 呼叫式預設值一律視為潛在的可變預設值陷阱，
#: 即使實際傳入的是空的不可變 tuple 也一樣）；抽成模組層級單例即可讓 ruff
#: 判定這不是每次呼叫都重新求值的函式呼叫式預設值。
_CLEAR_OPTION = typer.Option((), "--clear", help="要移除的全域鍵，可重複指定")


@config_app.command("set-global")
def config_set_global(
    download_dir: str | None = typer.Option(None, "--download-dir", hidden=True),
    upload_dir: str | None = typer.Option(None, "--upload-dir", hidden=True),
    max_attachment_mb: int | None = typer.Option(
        None, "--max-attachment-mb", help="單一附件大小上限（MB，需大於 0）"
    ),
    clear: list[str] = _CLEAR_OPTION,
    yes: bool = typer.Option(False, "--yes", help="跳過寫入前的確認"),
) -> None:
    """修改全域設定（各站台的預設值）。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard
    from redmine_mcp.setup.external import run_command

    if download_dir is not None or upload_dir is not None:
        typer.echo("附件目錄已改為自動管理，舊路徑選項不可再設定。", err=True)
        raise typer.Exit(1)
    raise typer.Exit(
        config_ops.set_global(
            wizard.console_prompts(),
            config_path=resolve_config_path(),
            max_attachment_mb=max_attachment_mb,
            clear=clear,
            run=run_command,
            assume_yes=yes,
        )
    )
