"""install.sh 的行為測試。

以假 HOME 跑真腳本：偵測、複製、標記區塊寫入都是檔案系統行為，用 mock 驗不到
真正會出錯的地方（IFS 拆解、awk 區塊比對、路徑引號）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import GUIDE_PATHS

#: 唯一的複製實作。
SCRIPT = Path(__file__).resolve().parents[1] / "install" / "install.sh"

#: 解析出實際的 bash 執行檔路徑，而非把字串 "bash" 原樣交給 subprocess。
#:
#: 在 Windows 上，Python 的 subprocess 交給 CreateProcess 找可執行檔時，
#: 會先查系統目錄（System32）再查 PATH 環境變數——這與 shell 自己（依 PATH
#: 順序）找到的 bash 可能是不同一支。若機器上也裝了 WSL，System32 底下有
#: 一支同名的 bash.exe（WSL 的啟動器），會被優先選中，導致實際執行的其實
#: 是 WSL 那支，它用 Linux 風格解析命令列參數，會把 Windows 路徑裡的反斜線
#: 當跳脫字元吃掉，讓路徑整個錯位、回報「找不到檔案」。改用 shutil.which
#: 取得的完整路徑可以固定住「就是這一支」，跨機器都可重現同一結果。
BASH = shutil.which("bash")

#: 沒有 bash 的環境（例如未裝 Git for Windows）跳過整個模組，
#: 而不是讓它紅——PowerShell 版由 test_install_ps1.py 負責。
pytestmark = pytest.mark.skipif(BASH is None, reason="環境沒有 bash")


#: 開發機上通常真的裝了 claude／gemini／opencode／copilot 等 CLI（這台機器本身
#: 就在跑 Claude Code），若原樣繼承 PATH，光靠 CLI 偵測就一定會命中，測不到
#: 「偵測不到任何 harness」或「只有某一類 harness」這種情境。改用只留 bash 與
#: 基本工具（find／sed／cp 等）的最小 PATH，讓測試環境與「乾淨機器」等價。
MINIMAL_PATH = "/usr/bin:/bin:/mingw64/bin"


def run_install(
    args: list[str], home: Path, *, minimal_path: bool = False
) -> subprocess.CompletedProcess[str]:
    """以假 HOME 執行 install.sh。

    參數:
        args: 傳給腳本的參數（不含 --home）。
        home: 充當 $HOME 的目錄。
        minimal_path: 是否收斂 PATH 成最小集合，避免開發機上裝的 CLI 干擾偵測。
    回傳:
        已完成的行程，stdout／stderr 為文字。
    """
    env = {**os.environ}
    if minimal_path:
        env["PATH"] = MINIMAL_PATH
    return subprocess.run(
        [BASH, str(SCRIPT), "--home", str(home), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_偵測到_claude_code_並歸為_native(tmp_path):
    # 家目錄標記這一路訊號：只建 .claude 目錄，不需要 PATH 上有 claude。
    (tmp_path / ".claude").mkdir()

    result = run_install(["--list"], tmp_path)

    assert result.returncode == 0, result.stderr
    rows = [line.split("\t") for line in result.stdout.splitlines()]
    assert ["claude-code", "native", ".claude/skills"] in rows


def test_偵測到_gemini_並歸為_fallback(tmp_path):
    (tmp_path / ".gemini").mkdir()

    result = run_install(["--list"], tmp_path)

    rows = [line.split("\t") for line in result.stdout.splitlines()]
    assert ["gemini-cli", "fallback", ".gemini/GEMINI.md"] in rows


def test_偵測到_continue_並歸為_manual(tmp_path):
    # continue 用 config.json／config.ts，沒有可寫入的 Markdown 指示檔。
    (tmp_path / ".continue").mkdir()

    result = run_install(["--list"], tmp_path)

    rows = [line.split("\t") for line in result.stdout.splitlines()]
    assert ["continue", "manual", ""] in rows


def test_空的_home_偵測不到任何家目錄標記(tmp_path):
    # 只會剩下靠 PATH 上的 CLI 命中的那幾列，不該出現任何依賴家目錄的 id。
    result = run_install(["--list"], tmp_path)

    ids = [line.split("\t")[0] for line in result.stdout.splitlines()]
    assert "continue" not in ids
    assert "amp" not in ids


def test_未知參數以_exit_2_拒絕(tmp_path):
    result = run_install(["--nope"], tmp_path)

    assert result.returncode == 2
    assert "未知的參數" in result.stderr


@pytest.mark.parametrize("flag", ["--only", "--home"])
def test_需要帶值的旗標沒帶值時以_exit_2_拒絕(flag: str, tmp_path):
    # `--only) shift; ONLY="$1"` 在 set -u 下，旗標若是最後一個參數會噴 bash
    # 自己的「$1: unbound variable」並 exit 1，而不是這支腳本的錯誤契約。
    # ps1 由參數綁定擋掉同一件事，bash 要自己檢查。
    result = subprocess.run(
        [BASH, str(SCRIPT), flag],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "需要一個值" in result.stderr
    assert "unbound variable" not in result.stderr


def test_list_不受_only_影響(tmp_path):
    # --list 是選單的資料來源。被 --only 篩過就沒得選了，所以偵測階段不套用它。
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".gemini").mkdir()

    result = run_install(["--list", "--only", "claude-code"], tmp_path)

    ids = [line.split("	")[0] for line in result.stdout.splitlines()]
    assert "claude-code" in ids
    assert "gemini-cli" in ids, "--list 不該被 --only 篩掉"


#: 原生類的安裝目錄，相對於假 HOME。
SKILL_REL = ".claude/skills/redmine-issue-writing"
CODEX_SKILL_REL = ".codex/skills/redmine-issue-writing"


def test_原生類把_SKILL_MD_與六份骨架複製到位(tmp_path):
    (tmp_path / ".claude").mkdir()

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    dest = tmp_path / SKILL_REL
    copied = {p.relative_to(dest).as_posix() for p in dest.rglob("*.md")}
    expected = {"SKILL.md"} | {f"references/{rel}" for rel in GUIDE_PATHS.values()}
    assert copied == expected, f"複製結果與來源不符：多了 {copied - expected}，少了 {expected - copied}"


def test_manifest_記下版本與每份檔案的_sha256(tmp_path):
    (tmp_path / ".claude").mkdir()

    run_install([], tmp_path)

    manifest = json.loads((tmp_path / SKILL_REL / ".installed.json").read_text("utf-8"))
    version = (Path(SCRIPT).resolve().parents[1] / "VERSION").read_text("utf-8").strip()
    assert manifest["version"] == version
    # 六份骨架加 SKILL.md，共七份。
    assert len(manifest["files"]) == 7
    assert all(len(sha) == 64 for sha in manifest["files"].values())
    assert manifest["pointers"] == []


def test_Codex_原生_skill_複製到位且不寫_AGENTS(tmp_path):
    (tmp_path / ".codex").mkdir()
    result = run_install(["--yes"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / CODEX_SKILL_REL / "SKILL.md").is_file()
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()


def test_重跑不改變_manifest(tmp_path):
    # 升版偵測靠 manifest 比對；每次重跑都產生不同內容會讓「被改過」永遠成立。
    (tmp_path / ".claude").mkdir()

    run_install([], tmp_path)
    first = (tmp_path / SKILL_REL / ".installed.json").read_text("utf-8")
    run_install([], tmp_path)
    second = (tmp_path / SKILL_REL / ".installed.json").read_text("utf-8")

    assert first == second


def test_沒有偵測到任何_harness_時以_exit_3_明講(tmp_path):
    # PATH 用最小集合，理由見 MINIMAL_PATH。
    #
    # returncode 一定要斷言：exit 3 是與呼叫端之間的契約——redmine-mcp 的
    # InstallSkillsStep.fix() 就是靠它分辨「安裝失敗」與「這台機器上沒有可裝
    # 的對象」。install.ps1 那邊原本用 Write-Error，在
    # $ErrorActionPreference = 'Stop' 下會提前以 exit 1 結束、根本走不到
    # exit 3，而只斷言訊息的測試完全看不出差異。兩平台都要驗到。
    result = run_install([], tmp_path, minimal_path=True)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "偵測不到" in result.stdout + result.stderr


#: 標記區塊的界定字串，與腳本內的字面值必須一致。
BEGIN_MARK = "<!-- redmine-issue-skills:begin -->"
END_MARK = "<!-- redmine-issue-skills:end -->"


def _gemini(home: Path, content: str) -> Path:
    """在假 HOME 建出一份帶既有內容的 GEMINI.md。"""
    path = home / ".gemini" / "GEMINI.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_後備類未給_yes_時只印出不寫入(tmp_path):
    # 往全域指示檔寫東西會影響使用者每一次對話，不該在他無法回答時發生。
    path = _gemini(tmp_path, "我自己寫的規則\n")

    result = run_install([], tmp_path)

    assert BEGIN_MARK not in path.read_text("utf-8")
    assert "要寫 Redmine 單時" in result.stdout, "應印出要加的那一行"


def test_後備類給了_yes_才寫入標記區塊(tmp_path):
    path = _gemini(tmp_path, "我自己寫的規則\n")

    run_install(["--yes"], tmp_path)

    text = path.read_text("utf-8")
    assert "我自己寫的規則" in text, "既有內容不可被動到"
    assert text.count(BEGIN_MARK) == 1
    assert text.count(END_MARK) == 1
    assert "~/.redmine-issue-guides/SKILL.md" in text


def test_後備類的骨架裝到_redmine_issue_guides(tmp_path):
    _gemini(tmp_path, "")

    run_install(["--yes"], tmp_path)

    guides = tmp_path / ".redmine-issue-guides"
    assert (guides / "SKILL.md").is_file()
    assert (guides / "references" / "bug_report" / "report.md").is_file()


def test_跑兩次不會加兩份標記區塊(tmp_path):
    path = _gemini(tmp_path, "我自己寫的規則\n")

    run_install(["--yes"], tmp_path)
    first = path.read_text("utf-8")
    run_install(["--yes"], tmp_path)
    second = path.read_text("utf-8")

    assert second.count(BEGIN_MARK) == 1
    assert first == second


def test_指示檔不存在時建出來(tmp_path):
    (tmp_path / ".gemini").mkdir()

    run_install(["--yes"], tmp_path)

    assert (tmp_path / ".gemini" / "GEMINI.md").is_file()


def test_uninstall_只刪標記區塊不碰區塊外的內容(tmp_path):
    path = _gemini(tmp_path, "區塊前的內容\n")
    run_install(["--yes"], tmp_path)
    path.write_text(path.read_text("utf-8") + "區塊後我又加的內容\n", encoding="utf-8")

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    text = path.read_text("utf-8")
    assert BEGIN_MARK not in text and END_MARK not in text
    assert "區塊前的內容" in text
    assert "區塊後我又加的內容" in text


def test_uninstall_刪掉兩個安裝目錄(tmp_path):
    (tmp_path / ".claude").mkdir()
    _gemini(tmp_path, "")
    run_install(["--yes"], tmp_path)

    run_install(["--uninstall"], tmp_path)

    assert not (tmp_path / SKILL_REL).exists()
    assert not (tmp_path / ".redmine-issue-guides").exists()


def test_uninstall_only_不刪別的_harness_還在用的骨架(tmp_path):
    # 骨架目錄 ~/.redmine-issue-guides 是後備類與 manual 類共用的一份。
    # `--uninstall --only claude-code` 若無條件把它刪掉，同機器上 Gemini CLI
    # 的指標就會指向不存在的檔案——而且不會有任何東西報錯，只有下一次對話時
    # agent 讀到 ENOENT。PATH 用最小集合，理由見 MINIMAL_PATH。
    (tmp_path / ".claude").mkdir()
    gemini = _gemini(tmp_path, "我自己寫的規則\n")
    run_install(["--yes"], tmp_path, minimal_path=True)
    guides = tmp_path / ".redmine-issue-guides"
    assert guides.is_dir(), "前置條件：後備類寫入後骨架目錄應該存在"

    result = run_install(
        ["--uninstall", "--only", "claude-code"], tmp_path, minimal_path=True
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / SKILL_REL).exists(), "被選中的 native 該被移除"
    assert guides.is_dir(), "還有沒被選到的後備類需要骨架，不可刪"
    assert BEGIN_MARK in gemini.read_text("utf-8"), "沒被選到的 harness 的指標該原樣留著"
    assert "保留" in result.stdout, "跳過刪除時要說明為什麼"


def test_uninstall_選取範圍涵蓋全部後備類時才刪骨架(tmp_path):
    # 上一條的反面：沒有人再用了就要真的清乾淨，否則「移除」只移一半。
    (tmp_path / ".claude").mkdir()
    _gemini(tmp_path, "我自己寫的規則\n")
    run_install(["--yes"], tmp_path, minimal_path=True)
    guides = tmp_path / ".redmine-issue-guides"
    assert guides.is_dir()

    result = run_install(
        ["--uninstall", "--only", "claude-code,gemini-cli"], tmp_path, minimal_path=True
    )

    assert result.returncode == 0, result.stderr
    assert not guides.exists(), "所有需要骨架的 harness 都在選取範圍內時就該刪掉"


def test_manual_類不碰它自己的設定但備妥骨架目錄(tmp_path):
    # manual 分支印的是「骨架已放在 ~/.redmine-issue-guides/SKILL.md」。那個
    # 路徑原本只在有 fallback harness 被同意寫入時才會被建立，只用 Cursor／
    # opencode／Copilot 的人照著做只會拿到 ENOENT——而且不會有任何錯誤訊息。
    # 因此契約是兩件事，缺一不可：
    #   1. manual harness 自己的目錄（.continue 等）一個位元組都不能被動到；
    #   2. 印出來的那個路徑必須真的存在。
    #
    # PATH 用最小集合，理由見 MINIMAL_PATH。
    (tmp_path / ".continue").mkdir()
    harness_dir = tmp_path / ".continue"
    before = {p.relative_to(harness_dir) for p in harness_dir.rglob("*")}

    result = run_install(["--yes"], tmp_path, minimal_path=True)

    assert result.returncode == 0, result.stderr
    assert "continue" in result.stdout
    after = {p.relative_to(harness_dir) for p in harness_dir.rglob("*")}
    assert after == before, "manual 類的 harness 自己的設定目錄不該被動到"

    guides = tmp_path / ".redmine-issue-guides"
    assert (guides / "SKILL.md").is_file(), "印出來的骨架路徑必須真的存在"
    assert (guides / "references" / "bug_report" / "report.md").is_file()
    assert (guides / ".installed.json").is_file(), "複製了就要寫 manifest，否則升版偵測沒有依據"


def test_only_只處理被選中的_harness(tmp_path):
    # 這條驗的不是「本任務新增的 fallback 寫入邏輯本身」——`--only claude-code`
    # 會讓 gemini 在進入 case 判斷之前就被既有的 _selected 擋掉，write_pointer
    # 根本不會被呼叫，所以覆蓋不到 write_pointer 的正確性。這條測試驗的是
    # 「既有的選取機制」與「本任務新增的 fallback 分支」共存時不會互相干擾：
    # 被排除的 gemini 既不會被寫入標記，也不該觸發本該只在真的有 fallback
    # 被處理時才發生的骨架複製（GUIDES_READY）。
    (tmp_path / ".claude").mkdir()
    path = tmp_path / ".gemini" / "GEMINI.md"
    path.parent.mkdir(parents=True)
    path.write_text("我自己寫的規則\n", encoding="utf-8")

    run_install(["--yes", "--only", "claude-code"], tmp_path)

    assert (tmp_path / SKILL_REL / "SKILL.md").is_file()
    assert BEGIN_MARK not in path.read_text("utf-8"), "未被選中的 harness 不該被寫入"
    assert not (tmp_path / ".redmine-issue-guides").exists(), "被排除的 fallback 不該觸發骨架複製"


def test_only_選中不存在的_id_時什麼都不做(tmp_path):
    (tmp_path / ".claude").mkdir()

    result = run_install(["--yes", "--only", "沒有這個 id"], tmp_path)

    assert not (tmp_path / SKILL_REL).exists()
    assert result.returncode == 0, "選不到東西不算失敗，只是沒有被選中的對象"


def test_只有_begin_沒有_end_時不吞掉後面的內容(tmp_path):
    # awk 的剔除／比對邏輯若沒擋著「有 begin 沒有對應 end」這種殘缺狀態
    # （使用者手動刪掉了 end，或前一次寫入中途被中斷），open 旗標一旦被設成 1
    # 就再也不會歸零，會把 begin 之後到檔尾的所有內容整段吞掉，包括使用者
    # 自己寫的規則——這正是硬要求第 3 條「區塊外一個字都不碰」要擋的破壞。
    # write_pointer（--yes 觸發）與 remove_pointer（--uninstall 觸發）用的
    # 是同一支剔除邏輯，兩條路徑都要驗到。
    #
    # 斷言與 test_install_ps1.py 的同名測試對等。只斷言「使用者那行字還在
    # 檔案裡」太弱：守衛被拿掉時，腳本仍可能靜默略過警告、在殘缺標記後面再
    # 疊一份新區塊並印出「已寫入指標」謊稱成功，而那行字只是位置往後移，
    # 子字串斷言驗不出差異。改用三個真正能區分「完全不動」與「靜默改寫」的
    # 訊號：逐位元組內容不變、stderr 有「內容疑似殘缺」警告、stdout 印的是
    # 「跳過」而非「已寫入」。
    broken = BEGIN_MARK + "\n使用者在殘缺區塊後自己寫的規則\n"

    yes_home = tmp_path / "yes"
    yes_path = _gemini(yes_home, broken)
    before_yes = yes_path.read_bytes()
    result_yes = run_install(["--yes"], yes_home)
    assert yes_path.read_bytes() == before_yes, (
        "write_pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同），"
        "不可靜默疊加新區塊"
    )
    assert "內容疑似殘缺" in result_yes.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"
    assert "跳過寫入指標：gemini-cli" in result_yes.stdout
    assert "已寫入指標：gemini-cli" not in result_yes.stdout, "殘缺標記時不可謊稱已寫入指標"

    uninstall_home = tmp_path / "uninstall"
    uninstall_path = _gemini(uninstall_home, broken)
    before_uninstall = uninstall_path.read_bytes()
    result = run_install(["--uninstall"], uninstall_home)
    assert result.returncode == 0, result.stderr
    assert uninstall_path.read_bytes() == before_uninstall, \
        "remove_pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同）"
    assert "內容疑似殘缺" in result.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"


def test_end_排在_begin_之前時不吞掉使用者內容(tmp_path):
    # 舊守衛只驗「END 存不存在」（grep -qF），順序不對照樣放行：剔除用的 awk
    # 一旦旗標被設成 1 就再也不會歸零，會把 BEGIN 之後到檔尾的內容整段吞掉，
    # 然後印出「已寫入指標」謊稱成功。install.ps1 用行號比較擋得住，bash 這邊
    # 不能比它寬鬆。斷言用三個真正能區分「完全不動」與「靜默改寫」的訊號：
    # 逐位元組內容不變、stderr 有警告、stdout 印的是「跳過」而非「已寫入」。
    broken = (
        "我的第一行規則\n"
        + END_MARK + "\n"
        + BEGIN_MARK + "\n"
        "使用者在殘缺區塊後自己寫的規則\n"
    )

    yes_home = tmp_path / "yes"
    yes_path = _gemini(yes_home, broken)
    before_yes = yes_path.read_bytes()
    result_yes = run_install(["--yes"], yes_home)
    assert yes_path.read_bytes() == before_yes, \
        "write_pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同）"
    assert "內容疑似殘缺" in result_yes.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"
    assert "跳過寫入指標：gemini-cli" in result_yes.stdout
    assert "已寫入指標：gemini-cli" not in result_yes.stdout, "殘缺標記時不可謊稱已寫入指標"

    uninstall_home = tmp_path / "uninstall"
    uninstall_path = _gemini(uninstall_home, broken)
    before_uninstall = uninstall_path.read_bytes()
    result_uninstall = run_install(["--uninstall"], uninstall_home)
    assert result_uninstall.returncode == 0, result_uninstall.stderr
    assert uninstall_path.read_bytes() == before_uninstall, \
        "remove_pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同）"
    assert "內容疑似殘缺" in result_uninstall.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"


#: 檔案裡已有兩組成對標記區塊的情境，供兩平台的「全部剔除」測試共用。
MULTI_BLOCK = (
    "A\n"
    + BEGIN_MARK + "\n舊的第一份指標\n" + END_MARK + "\n"
    "B\n"
    + BEGIN_MARK + "\n舊的第二份指標\n" + END_MARK + "\n"
    "C\n"
)


def test_已有多組標記區塊時全部剔除只留一份(tmp_path):
    # 硬要求第 2 條「絕不追加第二份」。只處理第一組會在檔案裡留下兩個 begin，
    # 之後的移除與升版都會踩到殘缺狀態。
    path = _gemini(tmp_path, MULTI_BLOCK)

    run_install(["--yes"], tmp_path)

    text = path.read_text("utf-8")
    assert text.count(BEGIN_MARK) == 1, "重跑後不該留下第二組標記區塊"
    assert text.count(END_MARK) == 1
    assert "舊的第一份指標" not in text and "舊的第二份指標" not in text
    for kept in ("A", "B", "C"):
        assert kept in text.splitlines(), f"區塊外的「{kept}」不該被動到"


def test_uninstall_把多組標記區塊全部刪掉(tmp_path):
    path = _gemini(tmp_path, MULTI_BLOCK)

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    text = path.read_text("utf-8")
    assert BEGIN_MARK not in text and END_MARK not in text, "不可留下孤兒標記"
    assert text.splitlines() == ["A", "B", "C"], "區塊外的內容一個字都不該被動到"


#: 「標記字面值出現在行尾」的輸入。對腳本而言那是使用者自己寫的內容，不是標記
#: ——真正的標記必須自成一行。兩平台的測試共用同一份輸入，逐字對等。
TRAILING_TEXT_LINES = [
    "我的規則",
    BEGIN_MARK + " 尾巴",
    "區塊內",
    END_MARK,
    "之後",
]
TRAILING_TEXT_INPUT = "".join(line + "\n" for line in TRAILING_TEXT_LINES)

#: 寫進指示檔的那一行，與兩支腳本的字面值一致。
POINTER_LINE = "要寫 Redmine 單時，先讀 ~/.redmine-issue-guides/SKILL.md。"


def test_標記行尾有其他字時不被當成標記(tmp_path):
    # bash 版原本用 awk 的 index() 做子字串比對，會把「<!-- …begin --> 尾巴」
    # 這種行尾帶字的行當成區塊起點，連同「區塊內」與那行偽標記整段剔掉——
    # 使用者自己寫的內容就這樣消失了，違反硬要求第 3 條「區塊外一個字都不碰」。
    # ps1 版一直是整行 -eq，對同一份輸入一字未動；兩平台不可分歧。
    #
    # 不逐位元組比對而逐行比對：ps1 的 WriteAllLines 會寫成平台原生的 CRLF，
    # 位元組層級本來就與 bash 的 LF 不同（既有的兩平台對等測試也一律逐行比對）。
    # 逐行同樣驗得到「有沒有掉內容」，那才是這條要守的東西。
    path = _gemini(tmp_path, TRAILING_TEXT_INPUT)

    run_install(["--yes"], tmp_path)

    lines = path.read_text("utf-8").splitlines()
    assert lines[: len(TRAILING_TEXT_LINES)] == TRAILING_TEXT_LINES, \
        "行尾帶字的偽標記與它周圍的內容都是使用者的，一行都不該被剔掉"
    assert lines[len(TRAILING_TEXT_LINES) :] == ["", BEGIN_MARK, POINTER_LINE, END_MARK], \
        "新區塊應原樣追加在檔尾，且只有一份"
