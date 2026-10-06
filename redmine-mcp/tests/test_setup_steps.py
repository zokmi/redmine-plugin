"""安裝步驟的測試：每個步驟以假 CommandRunner 與假 Prompts 驗證判斷與動作。"""
import json
import os
from pathlib import Path

import pytest

from redmine_mcp.setup import steps
from redmine_mcp.setup.external import CommandResult
from redmine_mcp.setup.installer import STEPS
from redmine_mcp.setup.steps import (
    TOOL,
    ConfigStep,
    Context,
    InstallSkillsStep,
    PackageStep,
    RegisterStep,
    SmokeStep,
    StepOutcome,
    UvStep,
)
from redmine_mcp.setup.wizard import Prompts


@pytest.fixture(autouse=True)
def 清空_redmine_環境變數(monkeypatch):
    """把 REDMINE_* 環境變數清掉。

    參數檔步驟走的 load_settings() 預設會讀 os.environ，開發機上設過的金鑰或網址
    會蓋掉測試寫進 tmp_path 的參數檔，讓斷言驗到的是開發機的值。
    """
    for name in [key for key in os.environ if key.startswith("REDMINE_")]:
        monkeypatch.delenv(name, raising=False)


class 假執行器:
    """依序回傳預先安排的結果並記錄呼叫。"""

    def __init__(self, *results: CommandResult) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], stdin: str | None = None, timeout: float | None = None
    ) -> CommandResult:
        self.calls.append(argv)
        return self.results.pop(0) if self.results else CommandResult(0, "", "")


def 靜默提問(confirms: list[bool] | None = None) -> tuple[Prompts, list[str]]:
    """建立只收集輸出、確認一律照給定序列回答的 Prompts。"""
    said: list[str] = []
    答案 = list(confirms or [])
    return (
        Prompts(
            say=said.append,
            panel=lambda title, lines: said.extend([title, *lines]),
            ask=lambda label, default=None: "",
            ask_secret=lambda label: "",
            confirm=lambda label: 答案.pop(0) if 答案 else True,
            choose=lambda label, options: 0,
            choose_many=lambda label, options, default: list(default),
            open_url=lambda url: None,
        ),
        said,
    )


def 建立情境(
    run,
    *,
    mode="install",
    config_path=Path("config.toml"),
    prompts=None,
    bundle=None,
    platform="linux",
) -> Context:
    """組出一個步驟用的 Context。

    參數:
        run: 假的外部指令執行器。
        mode: 執行模式。
        config_path: 參數檔路徑。
        prompts: 自訂的 Prompts；要驗步驟印了什麼時傳靜默提問() 拿到的那一份。
        bundle: ZIP 解壓目錄；給了就是 bundle 模式。
        platform: `sys.platform` 的值；預設 linux。驗證 Windows 專屬行為
            （例如檔案鎖定時終止子行程）時傳 "win32"。
    回傳:
        可直接餵給步驟的 Context。
    """
    prompts = prompts or 靜默提問()[0]

    async def 不會被呼叫的驗證(url: str, api_key: str) -> str:
        raise AssertionError("這個測試不該打 Redmine")

    return Context(
        prompts=prompts,
        run=run,
        verify=不會被呼叫的驗證,
        config_path=config_path,
        mode=mode,
        platform=platform,
        bundle=bundle,
    )


def test_uv_可用時檢查通過且帶出版本():
    run = 假執行器(CommandResult(0, "uv 0.9.2\n", ""))
    outcome = UvStep().check(建立情境(run))
    assert outcome.ok is True
    assert "0.9.2" in outcome.detail


def test_uv_不可用時檢查失敗且修復只給指引():
    run = 假執行器(CommandResult(127, "", "找不到執行檔：uv"))
    ctx = 建立情境(run)
    assert UvStep().check(ctx).ok is False
    outcome = UvStep().fix(ctx)
    assert outcome.ok is False
    assert "astral.sh" in outcome.detail


def test_套件已安裝時預設模式做升級(monkeypatch):
    # 環境殘缺偵測現在對所有模式生效，這裡不是在測那件事，把它假成「正常」
    # 才不會多消耗一次固定順序的假回應佇列。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""), # check
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""), # fix 內重讀
        CommandResult(0, "", ""),                             # upgrade
        CommandResult(0, "redmine-mcp v0.9.0\n- redmine-mcp\n", ""), # 升級後回讀
    )
    ctx = 建立情境(run)
    # 預設模式下已安裝也回報「需要處理」，fix() 才會跑一次 upgrade。
    step = PackageStep()
    assert step.check(ctx).ok is False
    outcome = step.fix(ctx)
    assert outcome.ok is True
    assert run.calls[2] == ["uv", "tool", "upgrade", "redmine-mcp"]
    assert "0.9.0" in outcome.detail


def test_套件未安裝時做全新安裝():
    run = 假執行器(
        CommandResult(0, "", ""),                             # check：清單是空的
        CommandResult(0, "", ""),                             # fix 內重讀
        CommandResult(0, "", ""),                             # install
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""), # 安裝後回讀
    )
    ctx = 建立情境(run)
    step = PackageStep()
    assert step.check(ctx).ok is False
    assert step.fix(ctx).ok is True
    assert run.calls[2][:3] == ["uv", "tool", "install"]


def test_reinstall_模式先移除再安裝():
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""), # check
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""), # fix 內重讀
        CommandResult(0, "", ""),                             # uninstall
        CommandResult(0, "", ""),                             # install
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""), # 回讀
    )
    ctx = 建立情境(run, mode="reinstall")
    step = PackageStep()
    # reinstall 模式下即使已安裝也要視為「需要處理」，否則 fix 不會被呼叫。
    assert step.check(ctx).ok is False
    assert step.fix(ctx).ok is True
    assert run.calls[2] == ["uv", "tool", "uninstall", "redmine-mcp"]
    assert run.calls[3][:3] == ["uv", "tool", "install"]


def test_安裝失敗時把_stderr_帶進結果供排錯():
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(1, "", "error: Failed to resolve git reference"),
    )
    ctx = 建立情境(run)
    outcome = PackageStep().fix(ctx)
    assert outcome.ok is False
    assert "git reference" in outcome.detail


_可用參數檔 = """
[sites.main]
url = "https://redmine.example.com/redmine"
api_key = "secret-key-1234"
"""


def 建立提問情境(run, confirms, *, mode="install", platform="linux"):
    """建立一個帶有腳本化確認答案的情境，並一併回傳輸出過的字串。"""
    prompts, said = 靜默提問(confirms)
    base = 建立情境(run)
    return (
        Context(
            prompts=prompts,
            run=run,
            verify=base.verify,
            config_path=base.config_path,
            mode=mode,
            platform=platform,
        ),
        said,
    )


def test_參數檔可用時_install_模式仍要求確認(tmp_path):
    # install／reinstall 模式下，即使參數檔已存在且可解析，也要回 ok=False——
    # 否則 fix()（安裝精靈）永遠不會被呼叫，使用者完全看不到網址／金鑰那兩題，
    # 事後想換金鑰或新增站台也無從得知該重跑什麼。真正的「完全不問」是 --repair。
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    outcome = ConfigStep().check(建立情境(假執行器(), config_path=path, mode="install"))
    assert outcome.ok is False
    assert "main" in outcome.detail


def test_參數檔可用時_reinstall_模式同樣要求確認(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    outcome = ConfigStep().check(建立情境(假執行器(), config_path=path, mode="reinstall"))
    assert outcome.ok is False
    assert "main" in outcome.detail


def test_參數檔可用時_repair_模式維持現行的非互動檢查(tmp_path):
    # repair 模式的用途就是「完全不問，只檢查與修」，這條行為本次不能變。
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")

    async def 假驗證(url: str, api_key: str) -> str:
        return "kenny"

    prompts, _ = 靜默提問()
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=假驗證,
        config_path=path,
        mode="repair",
        platform="linux",
    )
    outcome = ConfigStep().check(ctx)
    assert outcome.ok is True
    assert outcome.skipped is True
    assert "main" in outcome.detail


def test_參數檔不存在時檢查失敗(tmp_path):
    outcome = ConfigStep().check(建立情境(假執行器(), config_path=tmp_path / "無.toml"))
    assert outcome.ok is False


def test_參數檔缺必填欄位時檢查失敗(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[sites.main]\nurl = "https://x/redmine"\n', encoding="utf-8")
    outcome = ConfigStep().check(建立情境(假執行器(), config_path=path))
    assert outcome.ok is False
    assert "api_key" in outcome.detail


def test_repair_模式會實際驗一次金鑰(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    驗過的: list[tuple[str, str]] = []

    async def 假驗證(url: str, api_key: str) -> str:
        驗過的.append((url, api_key))
        return "kenny"

    prompts, _ = 靜默提問()
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=假驗證,
        config_path=path,
        mode="repair",
        platform="linux",
    )
    outcome = ConfigStep().check(ctx)
    assert outcome.ok is True
    assert 驗過的 == [("https://redmine.example.com/redmine", "secret-key-1234")]


def test_repair_模式金鑰驗不過時檢查失敗且訊息不含金鑰(tmp_path):
    from redmine_mcp.errors import RedmineError

    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")

    async def 假驗證(url: str, api_key: str) -> str:
        raise RedmineError("401 認證失敗")

    prompts, _ = 靜默提問()
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=假驗證,
        config_path=path,
        mode="repair",
        platform="linux",
    )
    outcome = ConfigStep().check(ctx)
    assert outcome.ok is False
    assert "401" in outcome.detail
    assert "secret-key-1234" not in outcome.detail


def test_參數檔步驟的移除會刪檔但先問過(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    ctx, _ = 建立提問情境(假執行器(), [True], mode="uninstall")
    ctx = Context(
        prompts=ctx.prompts,
        run=ctx.run,
        verify=ctx.verify,
        config_path=path,
        mode="uninstall",
        platform="linux",
    )
    outcome = ConfigStep().remove(ctx)
    assert outcome is not None and outcome.ok is True
    assert not path.exists()


def test_參數檔步驟的移除在使用者拒絕時保留檔案(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    ctx, _ = 建立提問情境(假執行器(), [False], mode="uninstall")
    ctx = Context(
        prompts=ctx.prompts,
        run=ctx.run,
        verify=ctx.verify,
        config_path=path,
        mode="uninstall",
        platform="linux",
    )
    outcome = ConfigStep().remove(ctx)
    assert outcome is not None and outcome.ok is True
    assert path.exists()


def test_啟動測試通過時檢查成功():
    run = 假執行器(CommandResult(0, "", "INFO Redmine MCP server 啟動，站台：main\n"))
    outcome = SmokeStep().check(建立情境(run))
    assert outcome.ok is True


def test_啟動測試失敗時給出排錯指引():
    run = 假執行器(
        CommandResult(1, "", "設定錯誤：缺少必填設定 api_key\n"),
        CommandResult(1, "", "設定錯誤：缺少必填設定 api_key\n"),
    )
    ctx = 建立情境(run)
    assert SmokeStep().check(ctx).ok is False
    outcome = SmokeStep().fix(ctx)
    assert outcome.ok is False
    assert "SETUP.md" in outcome.detail


def test_套件被執行中的行程鎖住且重試仍失敗時給出可行動的指引(monkeypatch):
    """Windows 不允許覆蓋執行中的執行檔，這是升級時最常見的失敗。

    自動終止並重試一次仍失敗（例如被防毒軟體另外鎖住），此時才把「請手動關掉
    Claude Code」的指引丟回去；不可再繼續重試，也不可回報成功。
    """
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    鎖定訊息 = CommandResult(
        2,
        "",
        "error: failed to remove directory "
        r"`C:\Users\kenny\AppData\Roaming\uv\tools\redmine-mcp`"
        "：存取被拒。 (os error 5)",
    )
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # fix 內重讀
        鎖定訊息,  # 第一次升級：被鎖住
        CommandResult(0, "", ""),  # taskkill 終止子行程
        鎖定訊息,  # 終止後重試：仍被鎖住
    )
    outcome = PackageStep().fix(建立情境(run, platform="win32"))
    assert outcome.ok is False
    assert "Claude Code" in outcome.detail
    # 終止指令必須真的下過，且用精確映像名並排除自己的 PID，不能誤殺別的行程。
    終止呼叫 = [argv for argv in run.calls if argv[:2] == ["taskkill", "/F"]]
    assert len(終止呼叫) == 1
    assert 終止呼叫[0][:4] == ["taskkill", "/F", "/IM", "redmine-mcp.exe"]
    # 一共應該重試一次升級（連同第一次共兩次 uv tool upgrade 呼叫）。
    升級呼叫 = [argv for argv in run.calls if argv[:3] == ["uv", "tool", "upgrade"]]
    assert len(升級呼叫) == 2


def test_套件被鎖住但終止後重試成功則視為安裝成功(monkeypatch):
    """自動終止子行程後，檔案 handle 通常會在短暫等待後釋放，重試應該直接成功。"""
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # fix 內重讀
        CommandResult(
            2,
            "",
            "error: failed to remove directory "
            r"`C:\Users\kenny\AppData\Roaming\uv\tools\redmine-mcp`"
            "：存取被拒。 (os error 5)",
        ),  # 第一次升級：被鎖住
        CommandResult(0, "", ""),  # taskkill 終止子行程
        CommandResult(0, "", ""),  # 終止後重試：成功
        CommandResult(0, "redmine-mcp v0.9.0\n- redmine-mcp\n", ""),  # 升級後回讀
    )
    outcome = PackageStep().fix(建立情境(run, platform="win32"))
    assert outcome.ok is True
    assert "0.9.0" in outcome.detail
    終止呼叫 = [argv for argv in run.calls if argv[:2] == ["taskkill", "/F"]]
    assert len(終止呼叫) == 1


def test_英文語系的存取被拒訊息同樣認得出來(monkeypatch):
    # uv 的這段訊息由作業系統產生因而隨語系而異，判斷要靠與語系無關的 os error 5。
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    鎖定訊息 = CommandResult(
        2,
        "",
        r"error: failed to remove directory `C:\Users\x\AppData\Roaming\uv\tools"
        ": Access is denied. (os error 5)",
    )
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),
        鎖定訊息,
        鎖定訊息,
    )
    outcome = PackageStep().fix(建立情境(run))
    assert outcome.ok is False
    assert "Claude Code" in outcome.detail


def test_非鎖定失敗不會觸發終止行程(monkeypatch):
    """認證失敗、網路問題等其他失敗不該去砍 redmine-mcp.exe——那和問題無關。"""
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(1, "", "fatal: Authentication failed for repository"),
    )
    outcome = PackageStep().fix(建立情境(run))
    assert outcome.ok is False
    assert not any(argv[:2] == ["taskkill", "/F"] for argv in run.calls)


def test_移除時被鎖住但終止後重試成功則視為移除成功(monkeypatch):
    """--uninstall 持有鎖的同樣是 Claude Code 開的 MCP server 子行程（另一個
    PID）；自動結束它再重試，比要求使用者關掉視窗自己補跑
    `uv tool uninstall` 對非 IT 使用者友善得多。
    """
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    run = 假執行器(
        CommandResult(
            2,
            "",
            "error: failed to remove directory "
            r"`C:\Users\kenny\AppData\Roaming\uv\tools\redmine-mcp`"
            "：存取被拒。 (os error 5)",
        ),  # 第一次移除：被鎖住
        CommandResult(0, "", ""),  # taskkill 終止子行程
        CommandResult(0, "", ""),  # 終止後重試：成功
    )
    outcome = PackageStep().remove(建立情境(run, mode="uninstall", platform="win32"))
    assert outcome is not None
    assert outcome.ok is True
    assert "已移除" in outcome.detail
    終止呼叫 = [argv for argv in run.calls if argv[:2] == ["taskkill", "/F"]]
    assert len(終止呼叫) == 1
    assert 終止呼叫[0][:4] == ["taskkill", "/F", "/IM", "redmine-mcp.exe"]
    移除呼叫 = [argv for argv in run.calls if argv[:3] == ["uv", "tool", "uninstall"]]
    assert len(移除呼叫) == 2


def test_移除時被鎖住且重試仍失敗時給出可行動的指引(monkeypatch):
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    鎖定訊息 = CommandResult(
        2,
        "",
        "error: failed to remove directory "
        r"`C:\Users\kenny\AppData\Roaming\uv\tools\redmine-mcp`"
        "：存取被拒。 (os error 5)",
    )
    run = 假執行器(
        鎖定訊息,  # 第一次移除：被鎖住
        CommandResult(0, "", ""),  # taskkill 終止子行程
        鎖定訊息,  # 終止後重試：仍被鎖住
    )
    outcome = PackageStep().remove(建立情境(run, mode="uninstall", platform="win32"))
    assert outcome is not None
    assert outcome.ok is False
    assert outcome.detail == steps._鎖定指引
    終止呼叫 = [argv for argv in run.calls if argv[:2] == ["taskkill", "/F"]]
    assert len(終止呼叫) == 1


def test_鎖定指引點名自我覆蓋這個真正的成因():
    """指引不可只叫人關掉 Claude Code。

    `redmine-mcp install` 自己就是 uv 要覆蓋的那個 redmine-mcp.exe，這種鎖定
    關掉 Claude Code 也解不掉，只有改用安裝腳本（跑在 powershell.exe／sh 底下）
    才覆蓋得動。指引若漏掉這條出路，使用者會在「關掉再重跑」的迴圈裡打轉。
    """
    assert "install.cmd" in steps._鎖定指引
    assert "redmine-mcp.exe" in steps._鎖定指引


def test_移除時非鎖定失敗不會觸發終止行程(monkeypatch):
    """移除失敗若不是檔案鎖定（例如權限問題），不該去砍 redmine-mcp.exe。"""
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    run = 假執行器(CommandResult(1, "", "error: permission denied"))
    outcome = PackageStep().remove(建立情境(run, mode="uninstall", platform="win32"))
    assert outcome is not None
    assert outcome.ok is False
    assert not any(argv[:2] == ["taskkill", "/F"] for argv in run.calls)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("error: failed to remove directory ...：存取被拒。 (os error 5)", True),
        (": Access is denied. (os error 5)", True),
        (
            r"failed to copy file from C:\...\redmine-mcp.exe to "
            r"C:\Users\kenny\.local\bin\redmine-mcp.exe: 程程序序無無法法存存取取檔"
            "檔案案，，因因為為檔檔案案正正由由另另一一個個程程序序使使用用。。 (os error 32)",
            True,
        ),
        ("error: Failed to resolve git reference", False),
        ("fatal: Authentication failed for repository", False),
        ("error: Failed to fetch: dependency resolution failed", False),
    ],
)
def test_是被鎖住的判斷涵蓋_os_error_5_與_32(message, expected):
    # os error 5（存取被拒，常見於移除）與 os error 32（分享違規，常見於安裝／
    # 升級複製執行檔時，這是 transcript 真實撞到的那種）都要判斷為鎖定；
    # 不相干的失敗（認證、相依解析）不可被誤判。
    assert steps._是被鎖住(message) is expected


def test_套件被_os_error_32_鎖住且重試仍失敗時給出可行動的指引(monkeypatch):
    # 真實案例：複製 redmine-mcp.exe 時被鎖住，訊息是 "failed to copy file" 而
    # 非 "failed to remove"，因此判斷不能要求同時出現 "remove"。
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    鎖定訊息 = CommandResult(
        1,
        "",
        r"error: failed to copy file from C:\...\redmine-mcp.exe to "
        r"C:\Users\kenny\.local\bin\redmine-mcp.exe: 因為檔案正由另一個程序使用。"
        " (os error 32)",
    )
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # fix 內重讀
        鎖定訊息,  # 第一次升級：被鎖住
        CommandResult(0, "", ""),  # taskkill 終止子行程
        鎖定訊息,  # 終止後重試：仍被鎖住
    )
    outcome = PackageStep().fix(建立情境(run, platform="win32"))
    assert outcome.ok is False
    assert "Claude Code" in outcome.detail
    終止呼叫 = [argv for argv in run.calls if argv[:2] == ["taskkill", "/F"]]
    assert len(終止呼叫) == 1


def test_套件被_os_error_32_鎖住但終止後重試成功則視為安裝成功(monkeypatch):
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # fix 內重讀
        CommandResult(
            1,
            "",
            r"error: failed to copy file ... redmine-mcp.exe: 因為檔案正由另一個"
            "程序使用。 (os error 32)",
        ),  # 第一次升級：被鎖住
        CommandResult(0, "", ""),  # taskkill 終止子行程
        CommandResult(0, "", ""),  # 終止後重試：成功
        CommandResult(0, "redmine-mcp v0.9.0\n- redmine-mcp\n", ""),  # 升級後回讀
    )
    outcome = PackageStep().fix(建立情境(run, platform="win32"))
    assert outcome.ok is True
    assert "0.9.0" in outcome.detail


def test_移除時被_os_error_32_鎖住但終止後重試成功則視為移除成功(monkeypatch):
    monkeypatch.setattr(steps, "_鎖定重試等待秒數", 0)
    run = 假執行器(
        CommandResult(
            1,
            "",
            r"error: failed to copy file ... redmine-mcp.exe: 因為檔案正由另一個"
            "程序使用。 (os error 32)",
        ),  # 第一次移除：被鎖住
        CommandResult(0, "", ""),  # taskkill 終止子行程
        CommandResult(0, "", ""),  # 終止後重試：成功
    )
    outcome = PackageStep().remove(建立情境(run, mode="uninstall", platform="win32"))
    assert outcome is not None
    assert outcome.ok is True
    assert "已移除" in outcome.detail


def test_其他安裝失敗仍原樣帶出_uv_的訊息():
    # 只對檔案鎖定改寫訊息；其餘失敗照舊把 stderr 帶出來，否則排錯資訊會被吃掉。
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(1, "", "error: Failed to resolve git reference"),
    )
    outcome = PackageStep().fix(建立情境(run))
    assert outcome.ok is False
    assert "git reference" in outcome.detail
    assert "Claude Code" not in outcome.detail


def test_套件安裝前先告知這一步可能要幾分鐘():
    # capture_output=True 讓下載期間畫面完全沒有輸出，第一次安裝要抓 Python 與整棵
    # 相依樹，非技術使用者會以為當掉而按 Ctrl-C。動手前先講一句。
    run = 假執行器(
        CommandResult(0, "", ""),  # installed_version：尚未安裝
        CommandResult(0, "", ""),  # install_package
        CommandResult(0, f"{TOOL} v0.8.0", ""),  # installed_version：安裝後
    )
    prompts, said = 靜默提問()
    ctx = Context(
        prompts=prompts,
        run=run,
        verify=lambda url, key: None,
        config_path=Path("config.toml"),
        mode="install",
        platform="win32",
    )
    PackageStep().fix(ctx)

    assert any("幾分鐘" in line for line in said), said


def test_repair_模式已安裝且環境正常時維持跳過(monkeypatch):
    # 這是回歸防線：--repair 對「已安裝且能跑」的機器本來就該什麼都不做。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    run = 假執行器(CommandResult(0, f"{TOOL} v0.8.0\n- {TOOL}\n", ""))
    outcome = PackageStep().check(建立情境(run, mode="repair"))
    assert outcome.ok is True
    assert outcome.skipped is True


def test_repair_模式已安裝但環境殘缺時檢查失敗並點名原因(monkeypatch):
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: True)
    run = 假執行器(CommandResult(0, f"{TOOL} v0.8.0\n- {TOOL}\n", ""))
    outcome = PackageStep().check(建立情境(run, mode="repair"))
    assert outcome.ok is False
    assert "殘缺" in outcome.detail
    assert "pyvenv.cfg" in outcome.detail


def test_repair_模式環境殘缺時_fix_先卸再裝而非升級(monkeypatch):
    # 實測過 --force 修不好這種殘缺（uv 認為環境已存在就不重建），只有先整個
    # 移除再重裝才有效；不能沿用預設模式的 upgrade 路徑。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: True)
    run = 假執行器(
        CommandResult(0, f"{TOOL} v0.8.0\n- {TOOL}\n", ""),  # fix 內重讀
        CommandResult(0, "", ""),  # uv tool uninstall
        CommandResult(0, "", ""),  # uv tool install
        CommandResult(0, f"{TOOL} v0.8.0\n- {TOOL}\n", ""),  # 回讀
    )
    outcome = PackageStep().fix(建立情境(run, mode="repair"))
    assert outcome.ok is True
    assert run.calls[1] == ["uv", "tool", "uninstall", TOOL]
    assert run.calls[2][:3] == ["uv", "tool", "install"]


def test_repair_模式環境正常時_fix_仍走升級路徑(monkeypatch):
    # 只有殘缺才該先卸再裝；環境正常的話 repair 模式進了 fix()（例如其他原因）
    # 仍應維持原本的 upgrade 語意，不可被新邏輯誤觸發。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    run = 假執行器(
        CommandResult(0, f"{TOOL} v0.8.0\n- {TOOL}\n", ""),  # fix 內重讀
        CommandResult(0, "", ""),  # uv tool upgrade
        CommandResult(0, f"{TOOL} v0.9.0\n- {TOOL}\n", ""),  # 回讀
    )
    outcome = PackageStep().fix(建立情境(run, mode="repair"))
    assert outcome.ok is True
    assert run.calls[1] == ["uv", "tool", "upgrade", TOOL]


def test_步驟表把參數檔排在註冊之前():
    # 註冊完才建參數檔的話，使用者中途放棄就會留下一個指向不存在設定的 MCP
    # server，Claude Code 每次啟動都會紅字，因此參數檔必須排在註冊之前。
    titles = [step.title for step in STEPS]
    assert titles.index(ConfigStep.title) < titles.index(RegisterStep.title)
    assert titles.index(RegisterStep.title) < titles.index(SmokeStep.title)


def test_步驟表不再有獨立的_claude_cli_步驟():
    # Claude Code 已經是註冊清單裡的一列，不該再有一個「一定會跑」的獨立步驟。
    titles = [step.title for step in STEPS]
    assert "Claude Code CLI" not in titles
    assert len(titles) == 6


def test_ConfigStep_不自己註冊也不印總結(tmp_path, monkeypatch):
    # 首次安裝原本會看到兩次「完成」面板、server 被啟動兩次：run_setup 自己就做了
    # 註冊、冒煙測試與總結，回到 installer 又跑一次 RegisterStep／SmokeStep 並再印
    # 一次總結。對非技術使用者來說「到底裝完了沒」是真實困惑。註冊、冒煙與總結是
    # installer 步驟表的職責，ConfigStep 只該負責參數檔。
    收到的參數: dict = {}

    def 假精靈(prompts, **kwargs) -> int:
        收到的參數.update(kwargs)
        return 0

    monkeypatch.setattr(steps, "run_setup", 假精靈)
    prompts, _ = 靜默提問()
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=lambda url, key: None,
        config_path=tmp_path / "config.toml",
        mode="install",
        platform="linux",
    )
    outcome = ConfigStep().fix(ctx)

    assert outcome.ok is True
    assert 收到的參數["finish"] is False, "ConfigStep 必須要求精靈只做到參數檔為止"


def _裝好的_skill(dir_: Path) -> Path:
    """在 dir_ 造出一份看起來裝好的 skill（含 manifest）。"""
    (dir_ / "references" / "bug_report").mkdir(parents=True)
    (dir_ / "SKILL.md").write_text("skill", encoding="utf-8")
    target = dir_ / "references" / "bug_report" / "report.md"
    target.write_text("骨架", encoding="utf-8")
    import hashlib

    manifest = {
        "version": "0.1.0",
        "files": {
            "SKILL.md": hashlib.sha256(b"skill").hexdigest(),
            "references/bug_report/report.md": hashlib.sha256(
                "骨架".encode()
            ).hexdigest(),
        },
        "pointers": [],
    }
    (dir_ / ".installed.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dir_


def test_skill_沒裝時_check_回_False(tmp_path):
    step = InstallSkillsStep(skill_dir=tmp_path / "不存在")

    outcome = step.check(建立情境(假執行器()))

    assert outcome.ok is False
    assert "沒裝" in outcome.detail


def test_skill_已裝且雜湊相符時_repair_模式_check_回_True(tmp_path):
    # 雜湊比對是 --repair 專屬的非互動檢查（判斷 skill 有沒有被改壞），
    # 不再決定「要不要問人」——那件事改由下面的 install 模式測試涵蓋。
    step = InstallSkillsStep(skill_dir=_裝好的_skill(tmp_path / "skill"))

    outcome = step.check(建立情境(假執行器(), mode="repair"))

    assert outcome.ok is True
    assert outcome.skipped is True
    assert "0.1.0" in outcome.detail


def test_skill_已裝且雜湊相符時_install_模式仍回_False(tmp_path):
    # install／reinstall 模式一律要求 fix() 跑一次，多選清單才會每次出現——
    # 否則使用者事後多裝了一種 AI 工具（例如 Gemini CLI），只要雜湊沒變就永遠
    # 不會再被問一次要不要也讓它讀得到。
    step = InstallSkillsStep(skill_dir=_裝好的_skill(tmp_path / "skill"))

    outcome = step.check(建立情境(假執行器(), mode="install"))

    assert outcome.ok is False


def test_skill_預設檢查也認得_codex_原生目錄(tmp_path, monkeypatch):
    codex_dir = tmp_path / "codex" / "redmine-issue-writing"
    _裝好的_skill(codex_dir)
    monkeypatch.setattr(steps, "_DEFAULT_SKILL_DIR", tmp_path / "claude" / "redmine-issue-writing")
    monkeypatch.setattr(steps, "_CODEX_SKILL_DIR", codex_dir)

    outcome = InstallSkillsStep().check(建立情境(假執行器(), mode="repair"))

    assert outcome.ok is True
    assert outcome.skipped is True
    assert "0.1.0" in outcome.detail


def test_檔案被改過時_repair_模式_check_回_False_並點名檔案(tmp_path):
    # 靜默失敗的主要防線：使用者手改過骨架，模型讀到的就不是公司格式，而那不會
    # 有任何東西報錯。這個雜湊比對只在 --repair 模式下執行。
    skill = _裝好的_skill(tmp_path / "skill")
    (skill / "SKILL.md").write_text("我自己改的", encoding="utf-8")
    step = InstallSkillsStep(skill_dir=skill)

    outcome = step.check(建立情境(假執行器(), mode="repair"))

    assert outcome.ok is False
    assert "SKILL.md" in outcome.detail


def test_manifest_壞掉時_check_回_False_而不是拋例外(tmp_path):
    # 安裝器要能修壞掉的狀態，不能自己先炸掉。
    skill = _裝好的_skill(tmp_path / "skill")
    (skill / ".installed.json").write_text("{壞掉的 JSON", encoding="utf-8")
    step = InstallSkillsStep(skill_dir=skill)

    outcome = step.check(建立情境(假執行器()))

    assert outcome.ok is False


def test_fix_呼叫共用腳本做列表(tmp_path):
    # 假執行器() 無參數時全部回傳成功但 stdout 為空——list 階段解析出 0 個
    # harness，於是這一步該印說明並跳過，且完全不會再呼叫安裝腳本。
    run = 假執行器()
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")

    outcome = step.fix(建立情境(run))

    assert run.calls, "fix() 未呼叫任何外部指令"
    assert run.calls[0][:2] == ["git", "clone"]
    assert outcome.ok is True
    assert outcome.skipped is True
    # list 階段的 clone／腳本各一次，沒有偵測到任何 harness 就不該再多打任何指令。
    assert len(run.calls) == 2


def test_偵測成功但一個都沒有時降級為略過並印出手動指令(tmp_path):
    # skill 只是加分項，MCP server 本身完全可用。腳本執行成功（code 0）但
    # 沒印出任何一行——這是「機器上真的沒有能裝的對象」，不是偵測本身壞了，
    # 訊息要講「沒有偵測到」而不是「偵測失敗」。
    run = 假執行器(CommandResult(0, "", ""), CommandResult(0, "", ""))
    prompts, said = 靜默提問()
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")

    outcome = step.fix(建立情境(run, prompts=prompts))

    assert outcome.ok is True
    assert outcome.skipped is True
    輸出 = "\n".join(said)
    assert "沒有偵測到" in 輸出, "必須讓使用者知道這一步被跳過了"
    assert "偵測 AI 工具時失敗" not in 輸出, "執行成功時不該講成偵測失敗"
    assert "redmine-issue-skills" in 輸出, "必須告訴使用者怎麼自己補裝"


def test_list階段腳本本身失敗時印出失敗訊息而不是含糊的沒有偵測到(tmp_path):
    # 腳本本身跑失敗（非 clone 階段）：這與「執行成功但一個都沒有」是不同的話術，
    # 使用者要看得到原始錯誤才有機會自己排查。
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(3, "", "偵測不到任何支援的 agent"),
    )
    prompts, said = 靜默提問()
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")

    outcome = step.fix(建立情境(run, prompts=prompts))

    assert outcome.ok is True
    assert outcome.skipped is True
    輸出 = "\n".join(said)
    assert "偵測 AI 工具時失敗" in 輸出
    assert "偵測不到任何支援的 agent" in 輸出, "使用者要看得到原始輸出才好排查"
    assert "redmine-issue-skills" in 輸出, "必須告訴使用者怎麼自己補裝"


def test_list階段_clone_失敗時也降級而不是擋住安裝且秀出原始訊息(tmp_path):
    # 第一個指令就是 git clone。沒設好憑證的機器會停在這裡——這正是非 bundle
    # 模式下最常見的真實病因。list_issue_skill_harnesses() 把這種失敗標成
    # failed=True 並帶回原始 stderr，這一步要把它印出來而不是吞掉，同時仍要
    # 繼續往下走、不擋住整個安裝。
    run = 假執行器(CommandResult(128, "", "fatal: Authentication failed"))
    prompts, said = 靜默提問()
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")

    outcome = step.fix(建立情境(run, prompts=prompts))

    assert outcome.ok is True
    assert outcome.skipped is True
    輸出 = "\n".join(said)
    assert "偵測 AI 工具時失敗" in 輸出
    assert "fatal: Authentication failed" in 輸出, "使用者要看得到原始 git 錯誤才排查得出病因"


def _三個_harness_的_list輸出() -> str:
    """組出 --list 會印出的三行，涵蓋 native／fallback／manual 三種類別。"""
    return (
        "claude-code\tnative\t.claude/skills\n"
        "gemini-cli\tfallback\t.gemini/GEMINI.md\n"
        "cursor\tmanual\t\n"
    )


def test_偵測到多個時_choose_many_被呼叫且只預設勾不碰檔案的(tmp_path):
    run = 假執行器(
        CommandResult(0, "", ""),  # list 階段：clone
        CommandResult(0, _三個_harness_的_list輸出(), ""),  # list 階段：腳本本身
        CommandResult(0, "", ""),  # 安裝階段：clone
        CommandResult(0, "", ""),  # 安裝階段：腳本本身
    )
    收到的呼叫: list[tuple[str, list[str], list[int]]] = []

    def choose_many(label, options, default):
        收到的呼叫.append((label, list(options), list(default)))
        return list(default)

    prompts, _ = 靜默提問()
    prompts = Prompts(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=choose_many,
        open_url=prompts.open_url,
    )
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")

    outcome = step.fix(建立情境(run, prompts=prompts))

    assert len(收到的呼叫) == 1
    _, 選項, 預設 = 收到的呼叫[0]
    assert len(選項) == 3
    # native（claude-code）不動使用者既有檔案，可以預設勾；fallback（gemini-cli
    # 會在 ~/.gemini/GEMINI.md 插入標記區塊）動的是使用者自己的檔案，manual 勾了
    # 也只是印步驟——兩者都不預設勾，要由使用者主動勾才發生。
    assert 預設 == [0], f"只有不碰既有檔案的才預設勾，實際拿到 {預設}"
    assert outcome.ok is True
    assert outcome.detail == "已安裝"
    最後一次呼叫 = run.calls[-1]
    assert "--only" in 最後一次呼叫
    只選的值 = 最後一次呼叫[最後一次呼叫.index("--only") + 1]
    assert 只選的值 == "claude-code"
    assert "--yes" in 最後一次呼叫


def test_使用者全部取消時跳過且不呼叫安裝(tmp_path):
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(0, _三個_harness_的_list輸出(), ""),
    )
    prompts, said = 靜默提問()
    prompts = Prompts(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: [],
        open_url=prompts.open_url,
    )
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")

    outcome = step.fix(建立情境(run, prompts=prompts))

    assert outcome.ok is True
    assert outcome.skipped is True
    # list 階段兩次呼叫之後就該停手，不該再多打任何安裝指令。
    assert len(run.calls) == 2
    assert "已取消" in "\n".join(said)


def test_只選部分時_only_只帶選中的_id(tmp_path):
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(0, _三個_harness_的_list輸出(), ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
    )
    prompts, _ = 靜默提問()
    prompts = Prompts(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: [1],  # 只選 gemini-cli
        open_url=prompts.open_url,
    )
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")

    step.fix(建立情境(run, prompts=prompts))

    最後一次呼叫 = run.calls[-1]
    只選的值 = 最後一次呼叫[最後一次呼叫.index("--only") + 1]
    assert 只選的值 == "gemini-cli"


def test_bundle_模式下偵測到多個時仍呼叫_choose_many_且預設全選(tmp_path):
    # 裁定：fallback 類會插入標記區塊到使用者既有的全域指示檔（如 GEMINI.md），
    # 那是使用者自己的檔案，不該在他沒選過的情況下被動到——即使他是 ZIP 一鍵
    # 安裝的非 IT 使用者。bundle 模式也必須真的顯示清單、真的問。
    run = 假執行器(
        CommandResult(0, _三個_harness_的_list輸出(), ""),
        CommandResult(0, "", ""),
    )
    收到的呼叫: list[tuple[str, list[str], list[int]]] = []

    def choose_many(label, options, default):
        收到的呼叫.append((label, list(options), list(default)))
        return list(default)

    prompts, _ = 靜默提問()
    prompts = Prompts(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=choose_many,
        open_url=prompts.open_url,
    )
    step = InstallSkillsStep(skill_dir=tmp_path / "skill")
    ctx = 建立情境(run, prompts=prompts, bundle=tmp_path)

    outcome = step.fix(ctx)

    assert len(收到的呼叫) == 1, "bundle 模式也必須真的呼叫 choose_many，不能跳過"
    _, 選項, 預設 = 收到的呼叫[0]
    assert len(選項) == 3
    assert 預設 == [0], "bundle 模式的預設勾選規則與一般模式相同"
    assert outcome.ok is True
    assert outcome.detail == "已安裝"
    最後一次呼叫 = run.calls[-1]
    只選的值 = 最後一次呼叫[最後一次呼叫.index("--only") + 1]
    assert 只選的值 == "claude-code"


def test_remove_帶_uninstall(tmp_path):
    run = 假執行器()
    step = InstallSkillsStep(skill_dir=_裝好的_skill(tmp_path / "skill"))

    outcome = step.remove(建立情境(run))

    assert outcome is not None
    assert "--uninstall" in run.calls[-1]


def test_remove_時_clone_失敗改印手動移除步驟而不是失敗(tmp_path):
    # 移除本身完全是本機操作，卻要先 clone repo 才拿得到腳本。離線或憑證失效
    # 時使用者就移不掉，而他當下要的只是把東西清掉——至少要告訴他手動怎麼做。
    run = 假執行器(CommandResult(128, "", "fatal: could not read Username"))
    prompts, said = 靜默提問()
    step = InstallSkillsStep(skill_dir=_裝好的_skill(tmp_path / "skill"))

    outcome = step.remove(建立情境(run, prompts=prompts))

    assert outcome is not None
    assert outcome.ok is True
    assert outcome.skipped is True
    輸出 = "\n".join(said)
    assert "could not read Username" in 輸出
    assert "redmine-issue-skills:begin" in 輸出, "手動步驟必須點名要刪的標記區塊"
    assert ".redmine-issue-guides" in 輸出


def test_bundle_模式下套件從本機安裝(tmp_path):
    run = 假執行器(CommandResult(1, "", "not installed"), CommandResult(0, "", ""))
    ctx = 建立情境(run, bundle=tmp_path)
    PackageStep().fix(ctx)
    安裝呼叫 = [argv for argv in run.calls if argv[:3] == ["uv", "tool", "install"]]
    assert 安裝呼叫, "應該有一次 uv tool install"
    assert str(tmp_path / "redmine-mcp") in 安裝呼叫[0]
    assert not any("git+https" in 參數 for 參數 in 安裝呼叫[0])


def test_bundle_模式下已安裝時走_force_重裝(tmp_path, monkeypatch):
    # 已安裝 → PackageStep.fix() 走升級路徑；bundle 模式的升級必須是本機 --force。
    # 環境殘缺偵測現在對所有模式生效，這裡不是在測那件事，假成「正常」。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    run = 假執行器(CommandResult(0, "redmine-mcp v0.3.0", ""))
    ctx = 建立情境(run, bundle=tmp_path)
    PackageStep().fix(ctx)
    assert any(
        argv[:3] == ["uv", "tool", "install"] and "--force" in argv for argv in run.calls
    )


def _建立_bundle_pyproject(bundle: Path, version: str) -> None:
    """在 bundle 目錄下造出一份 `redmine-mcp/pyproject.toml`，帶指定版本號。"""
    redmine_mcp = bundle / "redmine-mcp"
    redmine_mcp.mkdir(parents=True, exist_ok=True)
    (redmine_mcp / "pyproject.toml").write_text(
        f'[project]\nname = "redmine-mcp"\nversion = "{version}"\n', encoding="utf-8"
    )


def test_bundle_模式下已安裝版本與_bundle_相同時_check_回_skipped_且不呼叫安裝(tmp_path, monkeypatch):
    # install.ps1 的 [3/3] 已經用同一個 bundle 裝過一次；redmine-mcp install
    # --bundle 內部若不做這個判斷會再裝一次——這正是 transcript 裡「兩次
    # uv tool install，第二次撞上檔案鎖定」的成因。
    # 環境殘缺偵測現在對所有模式生效，這裡不是在測那件事，假成「正常」。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: False)
    _建立_bundle_pyproject(tmp_path, "0.11.0")
    run = 假執行器(CommandResult(0, "redmine-mcp v0.11.0\n- redmine-mcp\n", ""))
    ctx = 建立情境(run, bundle=tmp_path, mode="install")
    outcome = PackageStep().check(ctx)
    assert outcome.ok is True
    assert outcome.skipped is True
    assert not any(argv[:3] == ["uv", "tool", "install"] for argv in run.calls)
    # 逃生口要點名清楚：拿 repo 當 bundle 反覆測同一版本號的開發者，得知道
    # 要用 --reinstall 才能拿到剛改的程式碼。
    assert "--reinstall" in outcome.detail


def test_bundle_模式下已安裝版本與_bundle_不同時照裝(tmp_path):
    _建立_bundle_pyproject(tmp_path, "0.11.0")
    run = 假執行器(CommandResult(0, "redmine-mcp v0.10.0\n- redmine-mcp\n", ""))
    ctx = 建立情境(run, bundle=tmp_path, mode="install")
    outcome = PackageStep().check(ctx)
    assert outcome.ok is False


def test_bundle_模式下讀不到_pyproject_時保守照裝(tmp_path):
    # 沒有建立 redmine-mcp/pyproject.toml：讀檔失敗要回退成現行行為，不可因為
    # 讀不到就跳過安裝。
    run = 假執行器(CommandResult(0, "redmine-mcp v0.11.0\n- redmine-mcp\n", ""))
    ctx = 建立情境(run, bundle=tmp_path, mode="install")
    outcome = PackageStep().check(ctx)
    assert outcome.ok is False


def test_bundle_版本相同時_reinstall_模式不受影響(tmp_path):
    # --reinstall 的用途就是強制重裝，不該被「版本相同就跳過」擋下來。
    _建立_bundle_pyproject(tmp_path, "0.11.0")
    run = 假執行器(CommandResult(0, "redmine-mcp v0.11.0\n- redmine-mcp\n", ""))
    ctx = 建立情境(run, bundle=tmp_path, mode="reinstall")
    outcome = PackageStep().check(ctx)
    assert outcome.ok is False
    assert "重新安裝" in outcome.detail


def test_bundle_版本相同但環境殘缺時仍走先卸再裝而非跳過(tmp_path, monkeypatch):
    # b01956e 加的「環境殘缺」判斷優先於「bundle 版本相同就跳過」：版本號對得
    # 上不代表 venv 是完整的。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: True)
    _建立_bundle_pyproject(tmp_path, "0.11.0")
    run = 假執行器(CommandResult(0, "redmine-mcp v0.11.0\n- redmine-mcp\n", ""))
    ctx = 建立情境(run, bundle=tmp_path, mode="repair")
    outcome = PackageStep().check(ctx)
    assert outcome.ok is False
    assert "殘缺" in outcome.detail


def test_bundle_模式下_install_模式版本相同但環境殘缺時不可被跳過遮蔽(tmp_path, monkeypatch):
    # 對應真實使用者今天撞到的情境：install.ps1 的 [3/3] 已經用同一個 bundle
    # 裝過一次；若那次留下殘缺 venv（receipt 版本照樣跟 bundle 內原始碼相同），
    # 而殘缺偵測只在 --repair 生效，bundle 版本比對會在 install 模式（一鍵安裝
    # 走的正是這個模式）搶先命中，讓使用者看到綠色的 OK，但 /mcp 其實連不上，
    # 而且沒有任何提示叫他去跑 --repair。殘缺偵測必須對 install 模式也生效，
    # 且排在 bundle 版本比對之前，check() 才會回 ok=False 而不是 skipped=True。
    monkeypatch.setattr(steps, "tool_env_broken", lambda tool, run: True)
    _建立_bundle_pyproject(tmp_path, "0.11.0")
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.11.0\n- redmine-mcp\n", ""),  # check
        CommandResult(0, "redmine-mcp v0.11.0\n- redmine-mcp\n", ""),  # fix 內重讀
        CommandResult(0, "", ""),  # uv tool uninstall
        CommandResult(0, "", ""),  # uv tool install（bundle 來源、--force）
        CommandResult(0, "redmine-mcp v0.11.0\n- redmine-mcp\n", ""),  # 回讀
    )
    ctx = 建立情境(run, bundle=tmp_path, mode="install")
    step = PackageStep()

    check_outcome = step.check(ctx)
    assert check_outcome.ok is False, "環境殘缺不可被 bundle 版本相同的跳過遮蔽"
    assert "殘缺" in check_outcome.detail

    fix_outcome = step.fix(ctx)
    assert fix_outcome.ok is True
    # run.calls[0] 是 check() 自己的 installed_version；run.calls[1] 是 fix()
    # 內重讀的 before，接著才是真正的卸載／重裝。
    assert run.calls[2] == ["uv", "tool", "uninstall", "redmine-mcp"]
    assert run.calls[3][:3] == ["uv", "tool", "install"], "殘缺必須先卸載再重裝，不是升級"


def test_bundle_模式下_skill_不_clone(tmp_path):
    run = 假執行器()
    ctx = 建立情境(run, bundle=tmp_path)
    InstallSkillsStep(skill_dir=tmp_path / "skills").fix(ctx)
    assert not any(argv[0] == "git" for argv in run.calls)


def test_git_來源失敗且像認證問題時給_zip_指引():
    # ZIP 裝的機器沒有 GitHub 憑證，直接下 redmine-mcp install 必然撞上這個錯；
    # 只丟 uv 的原始訊息，使用者無從知道該做什麼。
    run = 假執行器(
        CommandResult(1, "", "not installed"),
        CommandResult(1, "", "fatal: could not read Username for 'https://github.com'"),
    )
    ctx = 建立情境(run)
    outcome = PackageStep().fix(ctx)
    assert not outcome.ok
    assert "下載新的 ZIP" in outcome.detail


def test_相依解析失敗不會被誤判為認證問題():
    # "not found" 這類通用字樣會出現在與認證無關的失敗裡；附上 ZIP 指引只會蓋掉病因。
    run = 假執行器(
        CommandResult(1, "", "not installed"),
        CommandResult(1, "", "error: distribution pycurl==7.47.0 not found in registry"),
    )
    ctx = 建立情境(run)
    outcome = PackageStep().fix(ctx)
    assert not outcome.ok
    assert "ZIP" not in outcome.detail


def _假偵測(*ids: str):
    """做一個回傳指定 harness 的假偵測函式。"""
    from redmine_mcp.setup.external import Harness, HarnessDetection

    def 偵測(run, *, platform, repo_dir=None):
        return HarnessDetection(tuple(Harness(id=i, kind="manual", instruction_file="") for i in ids))

    return 偵測


def test_註冊步驟只跑被勾中的註冊器(monkeypatch):
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(步驟模組, "list_issue_skill_harnesses", _假偵測("claude-code", "opencode"))
    跑過: list[str] = []

    class 假註冊器:
        會動到既有檔案 = False

        def __init__(self, harness_id):
            self.id = harness_id

        def 說明(self, ctx):
            return self.id

        def 已註冊(self, ctx):
            return False

        def 註冊(self, ctx):
            跑過.append(self.id)
            return StepOutcome(True, "ok")

        def 移除(self, ctx):
            跑過.append(f"移除{self.id}")
            return StepOutcome(True, "ok")

    monkeypatch.setattr(步驟模組, "取得註冊器", 假註冊器)
    prompts, _ = 靜默提問()
    # 靜默提問 的 choose_many 直接回傳 default，因此這條同時驗證了預設勾選規則：
    # 有自動寫入能力的（claude-code 在登錄表裡）預設勾，手動類（opencode）不勾。
    ctx = 建立情境(假執行器(), prompts=prompts)
    outcome = RegisterStep().fix(ctx)
    assert outcome.ok is True
    assert 跑過 == ["claude-code"], "手動類預設不該被勾中"


def test_一個都不勾時略過且不擋安裝(monkeypatch):
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(步驟模組, "list_issue_skill_harnesses", _假偵測("claude-code"))
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: [],
        open_url=prompts.open_url,
    )
    outcome = RegisterStep().fix(建立情境(假執行器(), prompts=prompts))
    assert outcome.ok is True and outcome.skipped is True


def test_偵測失敗時保底列出_claude_code(monkeypatch):
    # 註冊 MCP server 是本體功能，不能因為 skill 腳本壞掉就整個不做。
    from redmine_mcp.setup import steps as 步驟模組
    from redmine_mcp.setup.external import HarnessDetection

    monkeypatch.setattr(
        步驟模組,
        "list_issue_skill_harnesses",
        lambda run, *, platform, repo_dir=None: HarnessDetection((), failed=True, message="clone 失敗"),
    )
    看到的選項: list[str] = []
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: (看到的選項.extend(options), list(default))[1],
        open_url=prompts.open_url,
    )
    run = 假執行器(
        CommandResult(0, "2.1.4\n", ""),   # 說明用的 claude --version
        CommandResult(0, "2.1.4\n", ""),   # 註冊前的 claude --version
        CommandResult(0, "", ""),          # claude mcp add
    )
    outcome = RegisterStep().fix(建立情境(run, prompts=prompts))
    assert outcome.ok is True
    assert any("claude-code" in 選項 for 選項 in 看到的選項)


def test_只偵測到_gemini_時清單仍列出_claude_code(monkeypatch):
    # 只裝 Gemini CLI 的機器：偵測腳本不會印出 claude-code，但 spec 要求這一列
    # 永遠要出現在清單上，讓使用者自己決定要不要順便裝 Claude Code。
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(步驟模組, "list_issue_skill_harnesses", _假偵測("gemini-cli"))
    看到的選項: list[str] = []
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: (看到的選項.extend(options), [])[1],
        open_url=prompts.open_url,
    )
    RegisterStep().fix(建立情境(假執行器(), prompts=prompts))
    assert any("claude-code" in 選項 for 選項 in 看到的選項)


def test_只偵測到_gemini_時_claude_code_仍在預設勾選裡(monkeypatch):
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(步驟模組, "list_issue_skill_harnesses", _假偵測("gemini-cli"))
    看到的預設: list[list[int]] = []
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: (看到的預設.append(list(default)), [])[1],
        open_url=prompts.open_url,
    )
    RegisterStep().fix(建立情境(假執行器(), prompts=prompts))
    # claude-code 補在最前面（index 0），註冊器登錄表裡有它，因此預設勾選規則
    # 本來就會把它涵蓋進去。
    assert 0 in 看到的預設[0]


def test_會動到既有檔案的工具預設不勾(monkeypatch):
    """gemini-cli 會改使用者自己的 ~/.gemini/settings.json，這種事必須是他主動勾
    才發生，不能預設勾好等他取消。claude-code 走 CLI、不碰任何既有檔案，可以預設勾。
    """
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(
        步驟模組, "list_issue_skill_harnesses", _假偵測("claude-code", "gemini-cli")
    )
    收到: list[tuple[list[str], list[int]]] = []
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: (
            收到.append((list(options), list(default))),
            [],
        )[1],
        open_url=prompts.open_url,
    )
    RegisterStep().fix(建立情境(假執行器(), prompts=prompts))
    選項, 預設 = 收到[0]
    assert "claude-code" in 選項[0] and "gemini-cli" in 選項[1]
    assert 預設 == [0], f"只有不碰既有檔案的才預設勾，實際拿到 {預設}"


def test_註冊結果_skipped_不算成功_detail要標明(monkeypatch):
    # 使用者答「不要覆蓋」（或這一家只印手動步驟）時，回傳的是 ok=True 但
    # skipped=True；不可跟真正註冊成功的混在一起，否則總結會印出誤導性的
    # ✓ gemini-cli，讓使用者以為註冊上去了。
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(步驟模組, "list_issue_skill_harnesses", _假偵測("claude-code", "gemini-cli"))

    class 假註冊器:
        # 這條測的是成功／略過的彙整，不是預設勾選規則；兩家都標成不碰既有檔案，
        # 讓它們都落在預設勾選裡（靜默提問 的 choose_many 直接回傳 default）。
        會動到既有檔案 = False

        def __init__(self, harness_id):
            self.id = harness_id

        def 說明(self, ctx):
            return self.id

        def 已註冊(self, ctx):
            return False

        def 註冊(self, ctx):
            if self.id == "gemini-cli":
                return StepOutcome(True, "保留原設定", skipped=True)
            return StepOutcome(True, "scope: user")

        def 移除(self, ctx):
            return StepOutcome(True, "")

    monkeypatch.setattr(步驟模組, "取得註冊器", 假註冊器)
    prompts, _ = 靜默提問()
    outcome = RegisterStep().fix(建立情境(假執行器(), prompts=prompts))
    assert outcome.ok is True
    assert "claude-code" in outcome.detail
    assert "gemini-cli（保留原設定）" in outcome.detail


def test_部分失敗仍算過全部失敗才紅燈(monkeypatch):
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(步驟模組, "list_issue_skill_harnesses", _假偵測("a", "b"))

    # 這條測的是成功／失敗的彙整邏輯，不是預設勾選規則：id "a"／"b" 不是真正
    # 登錄表裡的 harness id，預設不會被勾中，因此這裡用 choose_many 明確全選，
    # 讓兩個候選都真的跑一次註冊。
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=lambda label, options, default: list(range(len(options))),
        open_url=prompts.open_url,
    )

    class 只有b會成功:
        會動到既有檔案 = False

        def __init__(self, harness_id):
            self.id = harness_id

        def 說明(self, ctx):
            return self.id

        def 已註冊(self, ctx):
            return False

        def 註冊(self, ctx):
            return StepOutcome(self.id == "b", "detail")

        def 移除(self, ctx):
            return StepOutcome(True, "")

    monkeypatch.setattr(步驟模組, "取得註冊器", 只有b會成功)
    assert RegisterStep().fix(建立情境(假執行器(), prompts=prompts)).ok is True

    class 全都失敗(只有b會成功):
        def 註冊(self, ctx):
            return StepOutcome(False, "boom")

    monkeypatch.setattr(步驟模組, "取得註冊器", 全都失敗)
    assert RegisterStep().fix(建立情境(假執行器(), prompts=prompts)).ok is False


def test_全部都已註冊時檢查通過(monkeypatch):
    from redmine_mcp.setup import steps as 步驟模組

    monkeypatch.setattr(步驟模組, "list_issue_skill_harnesses", _假偵測("claude-code"))
    run = 假執行器(CommandResult(0, "redmine:\n  Type: stdio\n", ""))
    # RegisterStep.check() 只有 repair 模式才會在「已註冊」時通過；install／
    # reinstall 一律回報「需要處理」讓多選清單每次都出現，這是設計取捨而非漏洞。
    assert RegisterStep().check(建立情境(run, mode="repair")).ok is True


def test_移除會對所有有註冊器的工具都跑一次(monkeypatch):
    from redmine_mcp.setup import steps as 步驟模組

    移除過: list[str] = []

    class 記錄移除:
        def __init__(self, harness_id):
            self.id = harness_id

        def 說明(self, ctx):
            return self.id

        def 已註冊(self, ctx):
            return True

        def 註冊(self, ctx):
            return StepOutcome(True, "")

        def 移除(self, ctx):
            移除過.append(self.id)
            return StepOutcome(True, "已拆除")

    monkeypatch.setattr(步驟模組, "註冊器登錄表", {"claude-code": 記錄移除("claude-code"),
                                                   "gemini-cli": 記錄移除("gemini-cli")})
    outcome = RegisterStep().remove(建立情境(假執行器(), mode="uninstall"))
    assert outcome is not None and outcome.ok is True
    assert sorted(移除過) == ["claude-code", "gemini-cli"]
