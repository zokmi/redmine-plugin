"""安裝流程編排的測試：以假步驟驗證進度輸出、中止行為與 exit code。"""
from pathlib import Path

from redmine_mcp.setup.installer import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PREREQUISITE,
    run_install,
)
from redmine_mcp.setup.steps import (
    Context,
    PackageStep,
    Step,
    StepOutcome,
    UvStep,
)
from redmine_mcp.setup.wizard import Prompts


class 假步驟(Step):
    """依預設答案回應 check／fix，並記錄被呼叫的順序。"""

    def __init__(self, title: str, *, 檢查通過: bool, 修復通過: bool = True) -> None:
        self.title = title
        self._檢查通過 = 檢查通過
        self._修復通過 = 修復通過
        self.紀錄: list[str] = []

    def check(self, ctx: Context) -> StepOutcome:
        self.紀錄.append("check")
        return StepOutcome(self._檢查通過, "ok" if self._檢查通過 else "壞了")

    def fix(self, ctx: Context) -> StepOutcome:
        self.紀錄.append("fix")
        return StepOutcome(self._修復通過, "修好了" if self._修復通過 else "修不好")


def 建立提問() -> tuple[Prompts, list[str]]:
    """建立只收集輸出、確認一律回 True 的 Prompts。"""
    said: list[str] = []
    return (
        Prompts(
            say=said.append,
            panel=lambda title, lines: said.extend([title, *lines]),
            ask=lambda label, default=None: "",
            ask_secret=lambda label: "",
            confirm=lambda label: True,
            choose=lambda label, options: 0,
            choose_many=lambda label, options, default: list(default),
            open_url=lambda url: None,
        ),
        said,
    )


#: 靜默提問 是 建立提問 的別名。本檔既有測試沿用 建立提問 這個名字（語意不動），
#: 本次新增的測試沿用 task brief／test_setup_steps.py 使用的 靜默提問 這個名字，
#: 兩者是同一份實作，避免重複維護一份邏輯相同的 Prompts 建構程式。
靜默提問 = 建立提問


class 假執行器:
    """依序回傳預先安排的結果並記錄呼叫；本次新增測試都用不到真正的結果，故
    無參數時一律回傳成功。寫法沿用 test_setup_steps.py 的同名 helper。
    """

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv, stdin=None, timeout=None):
        from redmine_mcp.setup.external import CommandResult

        self.calls.append(argv)
        return self.results.pop(0) if self.results else CommandResult(0, "", "")


async def 假驗證(url: str, api_key: str) -> str:
    """一律回傳登入帳號的假驗證函式，供不需要驗證細節的測試共用。

    參數:
        url: 站台網址（未使用）。
        api_key: API 金鑰（未使用）。
    回傳:
        固定的登入帳號字串。
    """
    return "kenny"


def 執行(steps, mode="install"):
    """以假步驟表跑一次流程，回傳 exit code 與輸出過的字串。"""
    prompts, said = 建立提問()

    async def 假驗證(url: str, api_key: str) -> str:
        return "kenny"

    code = run_install(
        prompts,
        config_path=Path("config.toml"),
        run=lambda argv, stdin=None: None,  # type: ignore[arg-type,return-value]
        verify=假驗證,
        mode=mode,
        platform="linux",
        steps=steps,
    )
    return code, said


def test_全部檢查通過時不呼叫任何修復且回傳零():
    steps = [假步驟("甲", 檢查通過=True), 假步驟("乙", 檢查通過=True)]
    code, _ = 執行(steps)
    assert code == EXIT_OK
    assert all(s.紀錄 == ["check"] for s in steps)


def test_進度行帶有編號與總數():
    steps = [假步驟("甲", 檢查通過=True), 假步驟("乙", 檢查通過=True)]
    _, said = 執行(steps)
    合併 = "\n".join(said)
    assert "[1/2]" in 合併 and "[2/2]" in 合併
    assert "甲" in 合併 and "乙" in 合併


def test_檢查未過時呼叫修復並繼續往下():
    steps = [假步驟("甲", 檢查通過=False), 假步驟("乙", 檢查通過=True)]
    code, _ = 執行(steps)
    assert code == EXIT_OK
    assert steps[0].紀錄 == ["check", "fix"]
    assert steps[1].紀錄 == ["check"]


def test_修復失敗時停在該步不再往下():
    steps = [
        假步驟("甲", 檢查通過=False, 修復通過=False),
        假步驟("乙", 檢查通過=True),
    ]
    code, said = 執行(steps)
    assert code == EXIT_FAILED
    assert steps[1].紀錄 == []
    assert any("修不好" in line for line in said)


def test_前置步驟失敗時回傳前置專用的_exit_code():
    # 第一步是 uv，它失敗代表環境缺前置而非流程出錯，要能被外層自動化區分。
    steps = [假步驟(UvStep.title, 檢查通過=False, 修復通過=False), 假步驟("乙", 檢查通過=True)]
    code, _ = 執行(steps)
    assert code == EXIT_PREREQUISITE


def test_成功時印出使用者必須自己做的兩件事():
    steps = [假步驟("甲", 檢查通過=True)]
    _, said = 執行(steps)
    合併 = "\n".join(said)
    assert "重開 Claude Code" in 合併
    assert "/mcp" in 合併


class 假移除步驟(Step):
    """remove() 依預設答案回應，用來驗證反向流程。"""

    def __init__(self, title: str, *, 成功: bool, detail: str = "") -> None:
        self.title = title
        self._成功 = 成功
        self._detail = detail
        self.移除過 = False

    def check(self, ctx: Context) -> StepOutcome:
        raise AssertionError("uninstall 不該呼叫 check")

    def fix(self, ctx: Context) -> StepOutcome:
        raise AssertionError("uninstall 不該呼叫 fix")

    def remove(self, ctx: Context) -> StepOutcome | None:
        self.移除過 = True
        return StepOutcome(self._成功, self._detail)


def test_移除以反向順序執行():
    甲 = 假移除步驟("甲", 成功=True)
    乙 = 假移除步驟("乙", 成功=True)
    順序: list[str] = []
    甲.remove = lambda ctx: (順序.append("甲"), StepOutcome(True, ""))[1]  # type: ignore[method-assign]
    乙.remove = lambda ctx: (順序.append("乙"), StepOutcome(True, ""))[1]  # type: ignore[method-assign]
    code, _ = 執行([甲, 乙], mode="uninstall")
    assert code == EXIT_OK
    assert 順序 == ["乙", "甲"]


def test_套件移除失敗時印出可貼的指令而不是假裝完成():
    步驟 = 假移除步驟(
        PackageStep.title, 成功=False, detail="error: failed to remove redmine-mcp.exe"
    )
    code, said = 執行([步驟], mode="uninstall")
    assert code == EXIT_FAILED
    合併 = "\n".join(said)
    assert "uv tool uninstall redmine-mcp" in 合併
    assert "執行中" in 合併


def test_skill_步驟失敗時整個流程仍走完並回_EXIT_OK(tmp_path):
    """真的把 InstallSkillsStep 放進步驟表，驗它不再是安裝流程的硬性 gate。

    這一步要 clone 一個 private repo，沒憑證／沒網路的機器一定失敗。它排在
    RegisterStep 之後、SmokeStep 之前，若照實回 ok=False，整個
    `redmine-mcp install` 會停在這裡連啟動測試都跑不到——而 skill 只是加分項，
    MCP server 本身完全可用。用假步驟驗不到這件事：擋不擋得住取決於
    InstallSkillsStep.fix() 自己回什麼，所以這裡放真的那一個。
    """
    from redmine_mcp.setup.external import CommandResult
    from redmine_mcp.setup.steps import InstallSkillsStep

    後續 = 假步驟("啟動測試", 檢查通過=True)
    steps = [InstallSkillsStep(skill_dir=tmp_path / "沒裝"), 後續]
    prompts, said = 建立提問()

    async def 假驗證(url: str, api_key: str) -> str:
        return "kenny"

    code = run_install(
        prompts,
        config_path=Path("config.toml"),
        run=lambda argv, stdin=None, timeout=None: CommandResult(
            128, "", "fatal: Authentication failed"
        ),
        verify=假驗證,
        mode="install",
        steps=steps,
        platform="linux",
    )

    assert code == EXIT_OK
    assert 後續.紀錄, "skill 步驟失敗不該讓後面的步驟被跳過"
    輸出 = "\n".join(said)
    # list_issue_skill_harnesses() 承諾 clone／腳本失敗時不拋例外，但改版後
    # 這種「偵測本身失敗」要把原始 stderr 秀給使用者看（而不是含糊地說
    # 「沒有偵測到」），這樣沒設好 git 憑證的人才查得出病因。
    assert "偵測 AI 工具時失敗" in 輸出, "偵測失敗時仍要讓使用者知道這一步被跳過了"
    assert "fatal: Authentication failed" in 輸出, "使用者要看得到原始 git 錯誤"
    assert "redmine-issue-skills" in 輸出, "必須告訴使用者怎麼自己補裝"


def test_bundle_不完整時給白話原因(tmp_path):
    from redmine_mcp.setup.installer import validate_bundle

    問題 = validate_bundle(tmp_path)
    assert 問題 is not None
    assert "解壓縮" in 問題


def test_bundle_完整時通過(tmp_path):
    from redmine_mcp.setup.installer import validate_bundle

    (tmp_path / "redmine-mcp").mkdir()
    (tmp_path / "redmine-mcp" / "pyproject.toml").write_text("", encoding="utf-8")
    assert validate_bundle(tmp_path) is None


class 記錄步驟:
    """把每次 check() 收到的 ctx.bundle 記下來的假步驟。"""

    title = "假步驟"

    def __init__(self, 收件匣: list[object]) -> None:
        """建立假步驟。

        參數:
            收件匣: 用來收集 ctx.bundle 的清單。
        """
        self.收件匣 = 收件匣

    def check(self, ctx):
        self.收件匣.append(ctx.bundle)
        return StepOutcome(True, "")

    def fix(self, ctx):
        raise AssertionError("check() 已回 ok，不該走到 fix()")

    def remove(self, ctx):
        return None


def test_bundle_會傳進_context(tmp_path):
    收到: list[object] = []
    prompts, _ = 靜默提問()
    run_install(
        prompts,
        config_path=tmp_path / "config.toml",
        run=假執行器(),
        verify=假驗證,
        steps=(記錄步驟(收到),),
        bundle=tmp_path,
    )
    assert 收到 == [tmp_path]


def test_bundle_模式的完成面板講升級方式(tmp_path):
    prompts, said = 靜默提問()
    run_install(
        prompts,
        config_path=tmp_path / "config.toml",
        run=假執行器(),
        verify=假驗證,
        steps=(記錄步驟([]),),
        bundle=tmp_path,
    )
    assert "下載新的 ZIP" in "\n".join(said)


def test_非_bundle_模式的完成面板不提_zip(tmp_path):
    prompts, said = 靜默提問()
    run_install(
        prompts,
        config_path=tmp_path / "config.toml",
        run=假執行器(),
        verify=假驗證,
        steps=(記錄步驟([]),),
    )
    assert "下載新的 ZIP" not in "\n".join(said)
