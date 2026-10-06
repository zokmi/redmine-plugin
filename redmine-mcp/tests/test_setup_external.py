import subprocess

from redmine_mcp.setup import external
from redmine_mcp.setup.external import (
    PACKAGE_SOURCE,
    CommandResult,
    Harness,
    claude_version,
    install_claude_cli,
    install_issue_skills,
    install_package,
    installed_version,
    is_registered,
    list_issue_skill_harnesses,
    register,
    resolve_executable,
    smoke_test,
    terminate_running_tool,
    tool_env_broken,
    uninstall_package,
    unregister,
    upgrade_package,
    uv_version,
)


class 假執行器:
    """記錄呼叫並回傳預先安排的結果。"""

    def __init__(self, *results: CommandResult) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], str | None]] = []
        #: 每次呼叫收到的 timeout；未指定為 None（代表使用預設逾時）。
        self.timeouts: list[float | None] = []

    def __call__(
        self, argv: list[str], stdin: str | None = None, timeout: float | None = None
    ) -> CommandResult:
        self.calls.append((argv, stdin))
        self.timeouts.append(timeout)
        return self.results.pop(0) if self.results else CommandResult(0, "", "")


def test_註冊指令固定使用_user_scope():
    run = 假執行器()
    register("redmine", "redmine-mcp", run)
    assert run.calls[0][0] == [
        "claude", "mcp", "add", "redmine", "--scope", "user", "--", "redmine-mcp",
    ]


def test_已註冊時_mcp_get_回傳零():
    run = 假執行器(CommandResult(0, "redmine:\n  Type: stdio\n", ""))
    assert is_registered("redmine", run) is True


def test_未註冊時_mcp_get_回傳非零():
    run = 假執行器(CommandResult(1, "", "No MCP server found"))
    assert is_registered("redmine", run) is False


def test_取消註冊帶_user_scope():
    run = 假執行器()
    unregister("redmine", run)
    assert run.calls[0][0] == ["claude", "mcp", "remove", "redmine", "--scope", "user"]


def test_冒煙測試以空_stdin_呼叫並看啟動訊息():
    run = 假執行器(CommandResult(0, "", "INFO Redmine MCP server 啟動，站台：main\n"))
    ok, message = smoke_test("redmine-mcp", run)
    assert ok is True
    assert "啟動" in message
    assert run.calls[0] == (["redmine-mcp"], "")


def test_冒煙測試失敗時回傳_stderr_供排錯():
    run = 假執行器(CommandResult(1, "", "設定錯誤：缺少必填設定 api_key\n"))
    ok, message = smoke_test("redmine-mcp", run)
    assert ok is False
    assert "api_key" in message


def test_冒煙測試_exit_零但沒有啟動訊息仍算失敗():
    run = 假執行器(CommandResult(0, "", ""))
    ok, _ = smoke_test("redmine-mcp", run)
    assert ok is False


def test_註冊指令一律使用工具名稱而非絕對路徑():
    # 用名稱註冊比絕對路徑可攜：日後重裝路徑變了也不必重註冊。resolve_executable()
    # 不查 PATH（該檢查已改由 wizard.py 以 `claude --version` 探測），因此這裡
    # 不需要、也不該再對 shutil.which 打樁。
    assert resolve_executable("redmine-mcp") == "redmine-mcp"


def test_uv_版本讀得到時回傳版本字串():
    run = 假執行器(CommandResult(0, "uv 0.9.2 (abc1234 2026-08-01)\n", ""))
    assert uv_version(run) == "0.9.2"
    assert run.calls[0][0] == ["uv", "--version"]


def test_uv_不存在時回傳_None():
    run = 假執行器(CommandResult(127, "", "找不到執行檔：uv"))
    assert uv_version(run) is None


def test_已安裝版本從_uv_tool_list_解析():
    output = "redmine-mcp v0.8.0\n- redmine-mcp\nllm-wiki-mcp v0.1.0\n- llm-wiki-mcp\n"
    run = 假執行器(CommandResult(0, output, ""))
    assert installed_version("redmine-mcp", run) == "0.8.0"
    assert run.calls[0][0] == ["uv", "tool", "list"]


def test_未安裝時已安裝版本為_None():
    run = 假執行器(CommandResult(0, "llm-wiki-mcp v0.1.0\n- llm-wiki-mcp\n", ""))
    assert installed_version("redmine-mcp", run) is None


def test_名稱是前綴時不可誤判為已安裝():
    # `redmine-mcp-extra v1.0.0` 不是 `redmine-mcp`；用 startswith 會誤判。
    run = 假執行器(CommandResult(0, "redmine-mcp-extra v1.0.0\n- redmine-mcp-extra\n", ""))
    assert installed_version("redmine-mcp", run) is None


def test_安裝指令帶正確的_git_來源():
    run = 假執行器()
    install_package("redmine-mcp", run)
    assert run.calls[0][0] == [
        "uv", "tool", "install", "--from", PACKAGE_SOURCE,
        "--refresh-package", "redmine-mcp", "redmine-mcp",
    ]


def test_來源字串含子目錄片段():
    # 少了 #subdirectory= 會裝到 repo 根目錄而失敗，這是文件警告過的踩雷點。
    assert PACKAGE_SOURCE.endswith("#subdirectory=redmine-mcp")


def test_升級與移除指令():
    run = 假執行器(CommandResult(0, "", ""), CommandResult(0, "", ""))
    upgrade_package("redmine-mcp", run)
    uninstall_package("redmine-mcp", run)
    assert run.calls[0][0] == ["uv", "tool", "upgrade", "redmine-mcp"]
    assert run.calls[1][0] == ["uv", "tool", "uninstall", "redmine-mcp"]


def test_claude_版本讀得到時回傳版本字串():
    run = 假執行器(CommandResult(0, "2.1.4 (Claude Code)\n", ""))
    assert claude_version(run) == "2.1.4"


def test_claude_不存在時回傳_None():
    run = 假執行器(CommandResult(127, "", "找不到執行檔：claude"))
    assert claude_version(run) is None


def test_安裝_claude_在_windows_走_powershell_端點():
    run = 假執行器()
    install_claude_cli(run, "win32")
    argv = run.calls[0][0]
    assert argv[0] == "powershell"
    # 刻意不帶 -ExecutionPolicy Bypass：執行原則管的是「從檔案載入的腳本」，
    # -Command 帶字串（irm | iex）本來就不受它管轄，加了沒有作用卻擴大授權表面。
    # 設計文件說「不可省」指的是 install.ps1 那條 -File 路徑，不是這裡。
    assert "-ExecutionPolicy" not in argv
    assert "https://claude.ai/install.ps1" in argv[-1]


def test_安裝_claude_在_posix_走_shell_端點():
    run = 假執行器()
    install_claude_cli(run, "linux")
    argv = run.calls[0][0]
    assert argv[0] == "sh"
    assert "https://claude.ai/install.sh" in argv[-1]


def test_冒煙測試比對的字串確實出現在啟動訊息裡():
    # smoke_test() 靠比對啟動 log 中的關鍵字判定成功。改掉 __main__ 那行訊息會讓
    # 它靜默退化成一律回報失敗，而其餘測試全部餵假字串，不會示警——這條斷言就是
    # 把那條隱性耦合釘住。
    from redmine_mcp.__main__ import STARTED_MESSAGE
    from redmine_mcp.setup.external import _STARTED_MARKER

    assert _STARTED_MARKER in STARTED_MESSAGE


def test_子行程的中文訊息不會被字碼頁毀掉(monkeypatch):
    """冒煙測試靠比對中文 marker 判定 server 起得來，中文必須完整傳回來。

    Python 對 stderr 預設用 backslashreplace：字碼頁表示不了中文時會逸出成
    `\\uXXXX` 的字面字串（英文版 Windows 的 cp1252 就是這樣），marker 就永遠比
    不中，冒煙測試會靜默退化成一律回報失敗。這裡把 PYTHONIOENCODING 設成 ascii
    重現那個環境。
    """
    import sys

    from redmine_mcp.setup.external import run_command

    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    result = run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('server 啟動')"]
    )
    assert "啟動" in result.stderr


def test_逾時訊息可行動並指出可重跑(monkeypatch):
    # README 自己承認「第一次要下載 Python 與相依套件，較慢屬正常；持續失敗請確認
    # 公司 proxy」——那正是會撞到逾時的情境。使用者拿到「uv 執行逾時」五個字時，
    # 不知道下一步該做什麼，也不知道已下載的部分不會白費。
    def 假逾時(*a, **k):
        raise subprocess.TimeoutExpired(cmd="uv", timeout=k.get("timeout", 0))

    monkeypatch.setattr(external.subprocess, "run", 假逾時)
    result = external.run_command(["uv", "tool", "install", "x"])

    assert result.code == 124
    assert "proxy" in result.stderr
    assert "重跑" in result.stderr


def test_套件安裝用較長的逾時():
    # uv tool install 第一次要下載 Python 與整棵相依樹，一般指令的逾時遠遠不夠。
    run = 假執行器()
    install_package("redmine-mcp", run)
    assert run.timeouts[0] == external.SLOW_COMMAND_TIMEOUT


def test_一般指令用預設逾時():
    run = 假執行器()
    register("redmine", "redmine-mcp", run)
    # 註冊只是本機一次 CLI 呼叫，不該套用給下載用的長逾時。
    assert run.timeouts[0] is None


def test_安裝_skill_在_Windows_走_PowerShell():
    # 一行指令與安裝器共用同一支腳本；平台只決定用哪個解譯器。
    run = 假執行器()

    external.install_issue_skills(run, platform="win32")

    argv = run.calls[-1][0]
    # 固定用 powershell 而非 pwsh：PowerShell 5.1 是 Windows 內建，pwsh 不一定有。
    assert argv[0] == "powershell"
    assert any("install.ps1" in a for a in argv), argv


def test_安裝_skill_在其他平台走_bash():
    run = 假執行器()

    external.install_issue_skills(run, platform="linux")

    argv = run.calls[-1][0]
    assert argv[0] == "bash"
    assert any("install.sh" in a for a in argv), argv


def test_安裝_skill_先取得_repo():
    # 骨架不在 wheel 裡，安裝器手上沒有副本；不先 clone 就沒有腳本可跑。
    run = 假執行器()

    external.install_issue_skills(run, platform="linux")

    assert run.calls[0][0][:2] == ["git", "clone"]


def test_安裝_skill_不自動寫入使用者的指示檔():
    # 不帶 --yes：後備類在非 TTY 下只印出要加的那一行。全域指示檔是使用者自己的
    # 檔案，不該由 redmine-mcp install 順手改掉。
    run = 假執行器()

    external.install_issue_skills(run, platform="linux")

    assert "--yes" not in run.calls[-1][0]


def test_clone_失敗時不再往下跑腳本():
    run = 假執行器(CommandResult(1, "", "fatal: repository not found"))

    result = external.install_issue_skills(run, platform="linux")

    assert result.code == 1
    assert len(run.calls) == 1, "clone 失敗還去跑腳本，只會得到看不懂的第二個錯誤"


def test_移除_skill_帶_uninstall_旗標():
    run = 假執行器()

    external.install_issue_skills(run, platform="linux", uninstall=True)

    assert "--uninstall" in run.calls[-1][0]


def test_安裝來源可覆寫為本機路徑():
    # ZIP 一鍵安裝時機器上沒有 GitHub 憑證，來源必須換成解壓出來的本機目錄。
    run = 假執行器()
    install_package("redmine-mcp", run, source="/tmp/bundle/redmine-mcp")
    assert run.calls[0][0] == [
        "uv", "tool", "install", "--from", "/tmp/bundle/redmine-mcp",
        "--refresh-package", "redmine-mcp", "redmine-mcp",
    ]


def test_未指定來源時沿用_git():
    run = 假執行器()
    install_package("redmine-mcp", run)
    assert run.calls[0][0][4] == PACKAGE_SOURCE


def test_本機來源的升級走_install_force():
    # uv tool upgrade 會回頭解析當初記錄的 git 來源，在沒有憑證的機器上必然失敗；
    # 本機來源的「升級」只能是帶 --force 的重裝。
    run = 假執行器()
    upgrade_package("redmine-mcp", run, source="/tmp/bundle/redmine-mcp")
    assert run.calls[0][0] == [
        "uv", "tool", "install", "--from", "/tmp/bundle/redmine-mcp", "--force",
        "--refresh-package", "redmine-mcp", "redmine-mcp",
    ]


def test_未指定來源時升級走_uv_tool_upgrade():
    run = 假執行器()
    upgrade_package("redmine-mcp", run)
    assert run.calls[0][0] == ["uv", "tool", "upgrade", "redmine-mcp"]


def test_有_repo_dir_時不_clone(tmp_path):
    # 這是 ZIP 路線的核心：clone 一個 private repo 需要憑證，非 IT 機器上沒有。
    run = 假執行器()
    install_issue_skills(run, platform="win32", repo_dir=tmp_path)
    所有參數 = [argv for argv, _ in run.calls]
    assert not any(argv[0] == "git" for argv in 所有參數)
    assert str(tmp_path / "redmine-issue-skills" / "install" / "install.ps1") in 所有參數[0]


def test_沒有_repo_dir_時仍_clone():
    run = 假執行器(CommandResult(0, "", ""))
    install_issue_skills(run, platform="linux")
    assert run.calls[0][0][:2] == ["git", "clone"]


def test_install_issue_skills_帶_only_與_assume_yes_的_sh_argv(tmp_path):
    run = 假執行器()
    install_issue_skills(
        run, platform="linux", repo_dir=tmp_path, only=["claude-code", "gemini-cli"], assume_yes=True
    )
    argv = run.calls[-1][0]
    assert argv[0] == "bash"
    assert "--only" in argv
    assert argv[argv.index("--only") + 1] == "claude-code,gemini-cli"
    assert "--yes" in argv


def test_install_issue_skills_帶_only_與_assume_yes_的_ps1_argv(tmp_path):
    run = 假執行器()
    install_issue_skills(
        run, platform="win32", repo_dir=tmp_path, only=["claude-code", "gemini-cli"], assume_yes=True
    )
    argv = run.calls[-1][0]
    assert argv[0] == "powershell"
    assert "-Only" in argv
    assert argv[argv.index("-Only") + 1] == "claude-code,gemini-cli"
    assert "-Yes" in argv


def test_install_issue_skills_未帶_only_時不加旗標(tmp_path):
    run = 假執行器()
    install_issue_skills(run, platform="linux", repo_dir=tmp_path)
    argv = run.calls[-1][0]
    assert "--only" not in argv
    assert "--yes" not in argv


def test_list_正常解析三欄():
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(
            0,
            "claude-code\tnative\t.claude/skills\n"
            "gemini-cli\tfallback\t.gemini/GEMINI.md\n",
            "",
        ),
    )
    detection = list_issue_skill_harnesses(run, platform="linux")
    assert detection.failed is False
    assert detection.harnesses == (
        Harness(id="claude-code", kind="native", instruction_file=".claude/skills"),
        Harness(id="gemini-cli", kind="fallback", instruction_file=".gemini/GEMINI.md"),
    )


def test_list_容忍尾隨的_carriage_return():
    # install.ps1 的輸出經過子行程仍可能帶 Windows 風格的行尾，即便 harnesses.tsv
    # 本身釘死 LF。
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(0, "claude-code\tnative\t.claude/skills\r\n", ""),
    )
    detection = list_issue_skill_harnesses(run, platform="win32")
    assert detection.harnesses == (
        Harness(id="claude-code", kind="native", instruction_file=".claude/skills"),
    )


def test_list_跳過空行():
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(
            0,
            "\nclaude-code\tnative\t.claude/skills\n\n\ngemini-cli\tfallback\t.gemini/GEMINI.md\n\n",
            "",
        ),
    )
    detection = list_issue_skill_harnesses(run, platform="linux")
    assert len(detection.harnesses) == 2


def test_list_欄位不足的行被跳過而不拋例外():
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(0, "殘缺的一行\nclaude-code\tnative\t.claude/skills\n", ""),
    )
    detection = list_issue_skill_harnesses(run, platform="linux")
    assert len(detection.harnesses) == 1
    assert detection.harnesses[0].id == "claude-code"


def test_list_clone_失敗時回傳_failed且帶原始訊息_而不拋例外():
    # 這是真實會發生的病因：非 bundle 模式要 clone 一個 private repo 才拿得到
    # skill 腳本，git 憑證設錯時就會停在這裡。呼叫端要能把 stderr 原文秀給
    # 使用者看，而不是含糊地說「沒有偵測到」。
    run = 假執行器(CommandResult(1, "", "fatal: could not read Username"))
    detection = list_issue_skill_harnesses(run, platform="linux")
    assert detection.failed is True
    assert detection.harnesses == ()
    assert detection.message == "fatal: could not read Username"


def test_list_腳本本身失敗時也回傳_failed():
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(3, "", "偵測不到任何支援的 agent"),
    )
    detection = list_issue_skill_harnesses(run, platform="linux")
    assert detection.failed is True
    assert detection.harnesses == ()
    assert detection.message == "偵測不到任何支援的 agent"


def test_list_失敗且沒有_stderr_時退回_stdout():
    run = 假執行器(CommandResult(2, "some stdout output", ""))
    detection = list_issue_skill_harnesses(run, platform="linux")
    assert detection.failed is True
    assert detection.message == "some stdout output"


def test_list_有_repo_dir_時不_clone(tmp_path):
    run = 假執行器(CommandResult(0, "claude-code\tnative\t.claude/skills\n", ""))
    detection = list_issue_skill_harnesses(run, platform="linux", repo_dir=tmp_path)
    assert not any(argv[0] == "git" for argv, _ in run.calls)
    assert len(detection.harnesses) == 1
    argv = run.calls[-1][0]
    assert "--list" in argv


def test_終止鎖檔工具在_win32_下排除自己的_pid():
    # 安裝器自己就是以 redmine-mcp.exe 執行；裸的 taskkill /F /IM redmine-mcp.exe
    # 會把安裝流程自己砍掉，因此必須用 /FI 過濾掉呼叫者自己的 PID。
    run = 假執行器(CommandResult(0, "", ""))
    result = terminate_running_tool("redmine-mcp", run, "win32")
    assert result is not None
    argv = run.calls[0][0]
    assert argv[0] == "taskkill"
    assert argv[:4] == ["taskkill", "/F", "/IM", "redmine-mcp.exe"]
    assert "/FI" in argv
    filtre = argv[argv.index("/FI") + 1]
    assert filtre.startswith("PID ne ")
    assert "/T" not in argv


def test_終止鎖檔工具在非_win32_平台完全不下指令():
    run = 假執行器()
    result = terminate_running_tool("redmine-mcp", run, "linux")
    assert result is None
    assert run.calls == []


def test_執行環境正常時_uv_tool_dir_成功且_pyvenv_cfg_存在(tmp_path):
    根目錄 = tmp_path / "uv" / "tools"
    (根目錄 / "redmine-mcp").mkdir(parents=True)
    (根目錄 / "redmine-mcp" / "pyvenv.cfg").write_text("home = /usr\n")
    run = 假執行器(CommandResult(0, f"{根目錄}\n", ""))
    assert tool_env_broken("redmine-mcp", run) is False
    assert run.calls[0][0] == ["uv", "tool", "dir"]


def test_執行環境殘缺時_uv_tool_dir_成功但_pyvenv_cfg_不存在(tmp_path):
    根目錄 = tmp_path / "uv" / "tools"
    (根目錄 / "redmine-mcp").mkdir(parents=True)
    # 刻意不建立 pyvenv.cfg，模擬安裝中途被中斷、venv 殘缺的情況。
    run = 假執行器(CommandResult(0, f"{根目錄}\n", ""))
    assert tool_env_broken("redmine-mcp", run) is True


def test_uv_tool_dir_失敗時保守回傳_False():
    # 取不到工具根目錄時無法判斷環境好壞；修復是破壞性動作（移除重裝），
    # 不確定時不能觸發。
    run = 假執行器(CommandResult(1, "", "找不到執行檔：uv"))
    assert tool_env_broken("redmine-mcp", run) is False


def test_uv_tool_dir_成功但輸出空白時保守回傳_False():
    run = 假執行器(CommandResult(0, "", ""))
    assert tool_env_broken("redmine-mcp", run) is False


def test_本機來源的安裝與升級都會_refresh_自己這個套件():
    """少了 --refresh-package，uv 會拿快取裡上一次建好的 wheel 交差。

    實測（2026-08-30，本機 bundle 安裝）：同一個路徑、同一個版本號 0.11.0 改了
    程式碼再裝，uv 印出 `Resolved 42 packages in 30ms` → 沒有 `Building` 那行 →
    `Installed 1 package`，看起來完全成功，但 venv 裡是上一次的程式碼。加上
    --refresh-package 之後才出現 `Building redmine-mcp @ file:///...`。
    這種失敗比裝不起來更難查，因為它不會報錯。
    """
    for 呼叫 in (
        lambda run: install_package("redmine-mcp", run, source="/tmp/bundle/redmine-mcp"),
        lambda run: upgrade_package("redmine-mcp", run, source="/tmp/bundle/redmine-mcp"),
    ):
        run = 假執行器()
        呼叫(run)
        argv = run.calls[0][0]
        assert "--refresh-package" in argv
        assert argv[argv.index("--refresh-package") + 1] == "redmine-mcp"
