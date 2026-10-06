"""install.ps1 的行為測試，與 install.sh 逐項對等。

兩支腳本讀同一份 harnesses.tsv、寫同一組標記字面值。對等性靠這裡的測試守住——
只改一邊會讓兩個平台的使用者拿到不同結果，而那不會有任何東西報錯。

參數命名說明：需求書原稿用 `-Home`，但 `$Home` 是 PowerShell 的自動變數
（唯讀），`param([string]$Home ...)` 在腳本一啟動、參數綁定階段就會直接丟出
「Cannot overwrite variable Home because it is read-only or constant.」，連
腳本本體都還沒開始跑。這裡改用 `-HomeDir`，與 install.ps1 的參數名一致。
這個參數只有測試會用到（redmine-mcp 的安裝器不會傳它），改名不影響外部契約。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

#: PowerShell 版的安裝腳本。
SCRIPT = Path(__file__).resolve().parents[1] / "install" / "install.ps1"

#: 優先用 pwsh（PowerShell 7+），沒有就退回 Windows 內建的 powershell。
_SHELL = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(_SHELL is None, reason="環境沒有 pwsh 或 powershell")

#: 標記區塊的界定字串，與兩支腳本的字面值必須一致。
BEGIN_MARK = "<!-- redmine-issue-skills:begin -->"
END_MARK = "<!-- redmine-issue-skills:end -->"

#: 原生類的安裝目錄，相對於假 HOME。
SKILL_REL = ".claude/skills/redmine-issue-writing"
CODEX_SKILL_REL = ".codex/skills/redmine-issue-writing"

#: 開發機上常常真的裝了 claude／gemini／opencode／copilot 等 CLI（這台機器
#: 本身就在跑 Claude Code），若原樣繼承 PATH，光靠 CLI 偵測就會命中，測不到
#: 「偵測不到任何 harness」或「manual 類真的什麼都沒寫」這類情境。改用只留
#: PowerShell 執行檔本身所在目錄與基本系統目錄的最小 PATH，讓測試環境與
#: 「乾淨機器」等價。與 test_install_sh.py 的同名手法一致。
_MINIMAL_PATH = r"C:\Windows\System32;C:\Windows"


def run_install(
    args: list[str], home: Path, *, minimal_path: bool = False
) -> subprocess.CompletedProcess[str]:
    """以假 HOME 執行 install.ps1。

    參數:
        args: 傳給腳本的參數（不含 -HomeDir）。
        home: 充當家目錄的目錄。
        minimal_path: 是否收斂 PATH 成最小集合，避免開發機上裝的 CLI 干擾偵測。
    回傳:
        已完成的行程，stdout／stderr 為文字。
    """
    env = {**os.environ}
    if minimal_path:
        env["PATH"] = _MINIMAL_PATH
    return subprocess.run(
        [
            _SHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-HomeDir",
            str(home),
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_List_的輸出格式與_bash_版一致(tmp_path):
    # 兩支腳本的 --list／-List 都輸出 `<id>\t<類別>\t<指示檔>`：要先列清單再問
    # 使用者選哪幾個的呼叫端得靠它，格式不能分平台各一套。（redmine-mcp 的安裝器
    # 目前不走這條，它直接跑不帶參數的安裝。）
    (tmp_path / ".claude").mkdir()

    result = run_install(["-List"], tmp_path)

    assert result.returncode == 0, result.stderr
    rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]
    assert ["claude-code", "native", ".claude/skills"] in rows


def test_List_把_codex_列為原生_skill(tmp_path):
    (tmp_path / ".codex").mkdir()
    result = run_install(["-List"], tmp_path)
    rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]
    assert ["codex", "native", ".codex/skills"] in rows


def test_Codex_原生_skill_複製到位且不寫_AGENTS(tmp_path):
    (tmp_path / ".codex").mkdir()
    result = run_install(["-Yes"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / CODEX_SKILL_REL / "SKILL.md").is_file()
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()


def test_偵測到_continue_並歸為_manual_空欄位保留(tmp_path):
    # PowerShell 的 -split "`t" 沒有 bash IFS 那種「連續 tab 視為單一分隔符」
    # 的坑，空欄位（continue 的指示檔欄）應該原樣保留成空字串，而不是造成
    # 欄位錯位。
    (tmp_path / ".continue").mkdir()

    result = run_install(["-List"], tmp_path)

    rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]
    assert ["continue", "manual", ""] in rows


def test_原生類複製到位並寫出_manifest(tmp_path):
    (tmp_path / ".claude").mkdir()

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    dest = tmp_path / SKILL_REL
    assert (dest / "SKILL.md").is_file()
    assert (dest / "references" / "bug_report" / "report.md").is_file()
    manifest = json.loads((dest / ".installed.json").read_text("utf-8"))
    assert len(manifest["files"]) == 7
    assert all(len(sha) == 64 for sha in manifest["files"].values())


def test_後備類未給_Yes_時只印出不寫入(tmp_path):
    path = tmp_path / ".gemini" / "GEMINI.md"
    path.parent.mkdir(parents=True)
    path.write_text("我自己寫的規則\n", encoding="utf-8")

    result = run_install([], tmp_path)

    assert BEGIN_MARK not in path.read_text("utf-8")
    assert "要寫 Redmine 單時" in result.stdout


def test_跑兩次不會加兩份標記區塊(tmp_path):
    path = tmp_path / ".gemini" / "GEMINI.md"
    path.parent.mkdir(parents=True)
    path.write_text("我自己寫的規則\n", encoding="utf-8")

    run_install(["-Yes"], tmp_path)
    first = path.read_text("utf-8")
    run_install(["-Yes"], tmp_path)
    second = path.read_text("utf-8")

    assert second.count(BEGIN_MARK) == 1
    assert first == second


def test_Only_只處理被選中的_harness(tmp_path):
    (tmp_path / ".claude").mkdir()
    path = tmp_path / ".gemini" / "GEMINI.md"
    path.parent.mkdir(parents=True)
    path.write_text("我自己寫的規則", encoding="utf-8")

    run_install(["-Yes", "-Only", "claude-code"], tmp_path)

    assert (tmp_path / SKILL_REL / "SKILL.md").is_file()
    assert BEGIN_MARK not in path.read_text("utf-8"), "未被選中的 harness 不該被寫入"


def test_Only_接受逗號分隔的多個_id(tmp_path):
    # install.sh 的介面是 `--only a,b`。`pwsh -File install.ps1 -Only a,b` 會把
    # `a,b` 當成一個字串元素綁進 [string[]]$Only——不報錯，只是誰都選不到，
    # 於是「只裝這兩個」靜默變成「什麼都沒裝」。兩支腳本的參數寫法必須通用。
    (tmp_path / ".claude").mkdir()
    path = _gemini(tmp_path, "我自己寫的規則\n")

    result = run_install(["-Yes", "-Only", "claude-code,gemini-cli"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / SKILL_REL / "SKILL.md").is_file()
    assert BEGIN_MARK in path.read_text("utf-8")


def test_List_不受_Only_影響(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".gemini").mkdir()

    result = run_install(["-List", "-Only", "claude-code"], tmp_path)

    ids = [line.split("\t")[0] for line in result.stdout.splitlines() if line.strip()]
    assert "claude-code" in ids
    assert "gemini-cli" in ids, "-List 不該被 -Only 篩掉"


def test_Uninstall_只刪標記區塊不碰區塊外的內容(tmp_path):
    path = tmp_path / ".gemini" / "GEMINI.md"
    path.parent.mkdir(parents=True)
    path.write_text("區塊前的內容\n", encoding="utf-8")
    run_install(["-Yes"], tmp_path)
    path.write_text(path.read_text("utf-8") + "區塊後我又加的內容\n", encoding="utf-8")

    run_install(["-Uninstall"], tmp_path)

    text = path.read_text("utf-8")
    assert BEGIN_MARK not in text
    assert "區塊前的內容" in text
    assert "區塊後我又加的內容" in text


def test_沒有偵測到任何_harness_時以_exit_3_明講(tmp_path):
    # returncode 一定要斷言，理由見 test_install_sh.py 的同名測試：這裡原本
    # 只斷言訊息，所以沒抓到「Write-Error 在 $ErrorActionPreference = 'Stop'
    # 下讓腳本以 exit 1 提前結束、下面的 exit 3 永遠執行不到」這個兩平台分歧。
    result = run_install([], tmp_path, minimal_path=True)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "偵測不到" in result.stdout + result.stderr


def test_未知參數以_exit_2_拒絕(tmp_path):
    # 與 test_install_sh.py 的同名測試對等。少了 param 區塊末尾那個
    # ValueFromRemainingArguments 的 $Rest，PowerShell 的參數綁定器會自己丟出
    # 英文的「A parameter cannot be found...」並以 exit 1 結束——訊息與 exit
    # code 都和 bash 版分歧。
    result = run_install(["-Nope"], tmp_path)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "未知的參數" in result.stderr


def test_Uninstall_Only_不刪別的_harness_還在用的骨架(tmp_path):
    # 與 test_install_sh.py 的同名測試對等。骨架目錄是後備類與 manual 類共用
    # 的一份，`-Uninstall -Only claude-code` 無條件刪掉它會讓同機器上 Gemini
    # CLI 的指標指向不存在的檔案，而且不會有任何東西報錯。
    (tmp_path / ".claude").mkdir()
    gemini = _gemini(tmp_path, "我自己寫的規則\n")
    run_install(["-Yes"], tmp_path, minimal_path=True)
    guides = tmp_path / ".redmine-issue-guides"
    assert guides.is_dir(), "前置條件：後備類寫入後骨架目錄應該存在"

    result = run_install(
        ["-Uninstall", "-Only", "claude-code"], tmp_path, minimal_path=True
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / SKILL_REL).exists(), "被選中的 native 該被移除"
    assert guides.is_dir(), "還有沒被選到的後備類需要骨架，不可刪"
    assert BEGIN_MARK in gemini.read_text("utf-8"), "沒被選到的 harness 的指標該原樣留著"
    assert "保留" in result.stdout, "跳過刪除時要說明為什麼"


def test_Uninstall_選取範圍涵蓋全部後備類時才刪骨架(tmp_path):
    (tmp_path / ".claude").mkdir()
    _gemini(tmp_path, "我自己寫的規則\n")
    run_install(["-Yes"], tmp_path, minimal_path=True)
    guides = tmp_path / ".redmine-issue-guides"
    assert guides.is_dir()

    result = run_install(
        ["-Uninstall", "-Only", "claude-code,gemini-cli"], tmp_path, minimal_path=True
    )

    assert result.returncode == 0, result.stderr
    assert not guides.exists(), "所有需要骨架的 harness 都在選取範圍內時就該刪掉"


def test_manual_類不碰它自己的設定但備妥骨架目錄(tmp_path):
    # 與 test_install_sh.py 的同名測試對等。manual 分支印的是「骨架已放在
    # ~/.redmine-issue-guides/SKILL.md」。那個路徑原本只在有 fallback harness
    # 被同意寫入時才會被建立，只用 Cursor／opencode／Copilot 的人照著做只會
    # 拿到 ENOENT——而且不會有任何錯誤訊息。因此契約是兩件事，缺一不可：
    #   1. manual harness 自己的目錄（.continue 等）一個位元組都不能被動到；
    #   2. 印出來的那個路徑必須真的存在。
    #
    # PATH 用最小集合，理由見 _MINIMAL_PATH。
    (tmp_path / ".continue").mkdir()
    harness_dir = tmp_path / ".continue"
    before = {p.relative_to(harness_dir) for p in harness_dir.rglob("*")}

    result = run_install(["-Yes"], tmp_path, minimal_path=True)

    assert result.returncode == 0, result.stderr
    assert "continue" in result.stdout
    after = {p.relative_to(harness_dir) for p in harness_dir.rglob("*")}
    assert after == before, "manual 類的 harness 自己的設定目錄不該被動到"

    guides = tmp_path / ".redmine-issue-guides"
    assert (guides / "SKILL.md").is_file(), "印出來的骨架路徑必須真的存在"
    assert (guides / "references" / "bug_report" / "report.md").is_file()
    assert (guides / ".installed.json").is_file(), "複製了就要寫 manifest，否則升版偵測沒有依據"


def _gemini(home: Path, content: str) -> Path:
    """在假 HOME 建出一份帶既有內容的 GEMINI.md。"""
    path = home / ".gemini" / "GEMINI.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_只有_begin_沒有_end_時不吞掉使用者內容(tmp_path):
    # 標記區塊殘缺（有 begin 沒有對應 end，可能是使用者手動刪掉 end，或前一次
    # 寫入中途被中斷）時，Write-Pointer／Remove-Pointer 都不可猜測要剔到哪裡
    # 為止，更不可把 begin 之後到檔尾的內容整段吞掉——那正是「區塊外一個字
    # 都不碰」這條硬要求要擋的破壞。write_pointer（-Yes 觸發）與
    # remove_pointer（-Uninstall 觸發）用的是同一支剔除邏輯，兩條路徑都要驗。
    #
    # 注意：只斷言「使用者那行字還在檔案裡」驗不到這個守衛——PowerShell 的
    # `$lines[$b..$e]`／逐索引過濾寫法在 $e = -1（即 $e -lt $b）時，數學上
    # 對每個索引 `$i -lt $b -or $i -gt $e` 恆為真，等同「保留所有行」，跟
    # bash 版用 awk 旗標狀態機、拿掉守衛真的會吞內容的行為完全不同。守衛被
    # 移除後，程式會靜默略過警告、在殘缺標記後面再疊一份新區塊（變成兩個
    # begin、一個 end 的更嚴重毀損狀態），並印出「已寫入指標」謊稱成功——
    # 但「使用者那行字」仍然還在檔案裡，只是位置往後移了，子字串斷言測不出
    # 差異。因此改用三個真正能區分「完全不動」與「靜默改寫」的訊號：
    # 逐位元組內容不變、stderr 有「內容疑似殘缺」警告、stdout 印的是「跳過」
    # 而非「已寫入」。
    broken = BEGIN_MARK + "\n使用者在殘缺區塊後自己寫的規則\n"

    yes_home = tmp_path / "yes"
    yes_path = _gemini(yes_home, broken)
    before_yes = yes_path.read_bytes()
    result_yes = run_install(["-Yes"], yes_home)
    after_yes = yes_path.read_bytes()
    assert after_yes == before_yes, (
        "Write-Pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同），"
        "不可靜默疊加新區塊"
    )
    assert "內容疑似殘缺" in result_yes.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"
    assert "跳過寫入指標：gemini-cli" in result_yes.stdout
    assert "已寫入指標：gemini-cli" not in result_yes.stdout, (
        "殘缺標記時不可謊稱已寫入指標"
    )

    uninstall_home = tmp_path / "uninstall"
    uninstall_path = _gemini(uninstall_home, broken)
    before_uninstall = uninstall_path.read_bytes()
    result_uninstall = run_install(["-Uninstall"], uninstall_home)
    after_uninstall = uninstall_path.read_bytes()
    assert result_uninstall.returncode == 0, result_uninstall.stderr
    assert after_uninstall == before_uninstall, (
        "Remove-Pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同）"
    )
    assert "內容疑似殘缺" in result_uninstall.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"


def test_end_排在_begin_之前時不吞掉使用者內容(tmp_path):
    # 與 test_install_sh.py 的同名測試對等。ps1 這邊原本用 `$e -lt $b` 就擋得住，
    # 改成 Get-MarkerSpan 的狀態機之後仍必須擋得住——這條守的是別在重構時放寬。
    broken = (
        "我的第一行規則\n"
        + END_MARK + "\n"
        + BEGIN_MARK + "\n"
        "使用者在殘缺區塊後自己寫的規則\n"
    )

    yes_home = tmp_path / "yes"
    yes_path = _gemini(yes_home, broken)
    before_yes = yes_path.read_bytes()
    result_yes = run_install(["-Yes"], yes_home)
    assert yes_path.read_bytes() == before_yes, \
        "Write-Pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同）"
    assert "內容疑似殘缺" in result_yes.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"
    assert "跳過寫入指標：gemini-cli" in result_yes.stdout
    assert "已寫入指標：gemini-cli" not in result_yes.stdout, "殘缺標記時不可謊稱已寫入指標"

    uninstall_home = tmp_path / "uninstall"
    uninstall_path = _gemini(uninstall_home, broken)
    before_uninstall = uninstall_path.read_bytes()
    result_uninstall = run_install(["-Uninstall"], uninstall_home)
    assert result_uninstall.returncode == 0, result_uninstall.stderr
    assert uninstall_path.read_bytes() == before_uninstall, \
        "Remove-Pointer 偵測到殘缺標記時必須完全不改動檔案（逐位元組相同）"
    assert "內容疑似殘缺" in result_uninstall.stderr, "偵測到殘缺標記時必須在 stderr 印出警告"


#: 檔案裡已有兩組成對標記區塊的情境。與 test_install_sh.py 的常數逐字對等。
MULTI_BLOCK = (
    "A\n"
    + BEGIN_MARK + "\n舊的第一份指標\n" + END_MARK + "\n"
    "B\n"
    + BEGIN_MARK + "\n舊的第二份指標\n" + END_MARK + "\n"
    "C\n"
)


def test_已有多組標記區塊時全部剔除只留一份(tmp_path):
    # 硬要求第 2 條「絕不追加第二份」。IndexOf 只取第一組時，這裡會留下
    # 「A、B、舊的第二份區塊、C、新區塊」——檔案裡有兩個 begin，而 bash 版
    # 產出的是「A B C ＋一個新區塊」。兩平台不可分歧。
    path = _gemini(tmp_path, MULTI_BLOCK)

    run_install(["-Yes"], tmp_path)

    text = path.read_text("utf-8")
    assert text.count(BEGIN_MARK) == 1, "重跑後不該留下第二組標記區塊"
    assert text.count(END_MARK) == 1
    assert "舊的第一份指標" not in text and "舊的第二份指標" not in text
    for kept in ("A", "B", "C"):
        assert kept in text.splitlines(), f"區塊外的「{kept}」不該被動到"


def test_Uninstall_把多組標記區塊全部刪掉(tmp_path):
    path = _gemini(tmp_path, MULTI_BLOCK)

    result = run_install(["-Uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    text = path.read_text("utf-8")
    assert BEGIN_MARK not in text and END_MARK not in text, "不可留下孤兒標記"
    assert text.splitlines() == ["A", "B", "C"], "區塊外的內容一個字都不該被動到"


#: 「標記字面值出現在行尾」的輸入。與 test_install_sh.py 的常數逐字對等。
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
    # 與 test_install_sh.py 的同名測試對等。ps1 用整行 -eq，本來就擋得住；
    # 這條守的是別在重構時放寬成子字串比對——那會把「區塊內」與那行行尾帶字
    # 的偽標記一起剔掉，也就是把使用者自己寫的內容吃掉。
    path = _gemini(tmp_path, TRAILING_TEXT_INPUT)

    run_install(["-Yes"], tmp_path)

    lines = path.read_text("utf-8").splitlines()
    assert lines[: len(TRAILING_TEXT_LINES)] == TRAILING_TEXT_LINES, \
        "行尾帶字的偽標記與它周圍的內容都是使用者的，一行都不該被剔掉"
    assert lines[len(TRAILING_TEXT_LINES) :] == ["", BEGIN_MARK, POINTER_LINE, END_MARK], \
        "新區塊應原樣追加在檔尾，且只有一份"
