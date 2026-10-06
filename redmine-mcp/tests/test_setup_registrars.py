"""各 AI 工具的 MCP 註冊器。"""
from __future__ import annotations

import json
from pathlib import Path

from redmine_mcp.setup.external import CommandResult
from redmine_mcp.setup.registrars import ClaudeCode註冊器, GeminiCLI註冊器, 取得註冊器, 手動註冊器
from tests.test_setup_steps import 假執行器, 建立情境


def test_claude_已註冊時回報已註冊():
    run = 假執行器(CommandResult(0, "redmine:\n  Type: stdio\n", ""))
    assert ClaudeCode註冊器().已註冊(建立情境(run)) is True


def test_claude_已安裝時直接註冊():
    run = 假執行器(
        CommandResult(0, "2.1.4 (Claude Code)\n", ""),  # claude --version
        CommandResult(1, "", "No MCP server found with name: redmine"),  # claude mcp get
        CommandResult(0, "", ""),                        # claude mcp add
    )
    結果 = ClaudeCode註冊器().註冊(建立情境(run))
    assert 結果.ok is True
    assert run.calls[-1] == [
        "claude", "mcp", "add", "redmine", "--scope", "user", "--", "redmine-mcp",
    ]


def test_claude_未安裝時先裝_cli_再註冊():
    # 勾選本身就是同意，不再多問一次「要裝嗎」。
    # 這條路徑不查 `claude mcp get`：CLI 是剛剛才裝上的，不可能有既有註冊。
    run = 假執行器(
        CommandResult(127, "", "找不到執行檔：claude"),   # claude --version
        CommandResult(0, "", ""),                        # 官方安裝端點
        CommandResult(0, "2.1.4 (Claude Code)\n", ""),   # 安裝後回讀
        CommandResult(0, "", ""),                        # claude mcp add
    )
    結果 = ClaudeCode註冊器().註冊(建立情境(run, platform="linux"))
    assert 結果.ok is True
    assert run.calls[1][0] == "sh", "非 Windows 走 sh 端點"
    assert run.calls[-1][:3] == ["claude", "mcp", "add"]


def test_claude_裝完仍找不到時回失敗且不試著註冊():
    run = 假執行器(
        CommandResult(127, "", "找不到執行檔：claude"),
        CommandResult(0, "", ""),
        CommandResult(127, "", "找不到執行檔：claude"),
    )
    結果 = ClaudeCode註冊器().註冊(建立情境(run))
    assert 結果.ok is False
    assert not any(argv[:3] == ["claude", "mcp", "add"] for argv in run.calls)


def test_claude_註冊指令失敗時把原始訊息帶回去():
    run = 假執行器(
        CommandResult(0, "2.1.4\n", ""),
        CommandResult(1, "", "No MCP server found with name: redmine"),  # claude mcp get
        CommandResult(1, "", "boom"),
    )
    結果 = ClaudeCode註冊器().註冊(建立情境(run))
    assert 結果.ok is False
    assert "boom" in 結果.detail


def test_claude_移除會拆掉註冊():
    run = 假執行器(
        CommandResult(0, "redmine:\n  Type: stdio\n", ""),  # 已註冊() 判斷
        CommandResult(0, "", ""),                           # claude mcp remove
    )
    結果 = ClaudeCode註冊器().移除(建立情境(run, mode="uninstall"))
    assert 結果.ok is True
    assert run.calls[-1] == ["claude", "mcp", "remove", "redmine", "--scope", "user"]


def test_claude_沒有_cli_時移除視為成功且不呼叫_remove():
    # 機器上根本沒有 claude 執行檔（claude mcp get 回 127），代表本來就沒有註冊，
    # 不該因為 `claude mcp remove` 也回 127 就把整個 --uninstall 判定成失敗。
    run = 假執行器(CommandResult(127, "", "找不到執行檔：claude"))
    結果 = ClaudeCode註冊器().移除(建立情境(run, mode="uninstall"))
    assert 結果.ok is True
    assert 結果.skipped is True
    assert not any(argv[:3] == ["claude", "mcp", "remove"] for argv in run.calls)


def test_claude_說明會講清楚不動既有檔案():
    說明 = ClaudeCode註冊器().說明(建立情境(假執行器(CommandResult(0, "2.1.4\n", ""))))
    assert "claude-code" in 說明
    assert "不動你既有的檔案" in 說明


def test_claude_未安裝時說明要講會幫忙裝():
    說明 = ClaudeCode註冊器().說明(建立情境(假執行器(CommandResult(127, "", ""))))
    assert "尚未安裝" in 說明


def _寫設定(tmp_path: Path, 內容: str) -> Path:
    """準備一份 gemini settings.json，回傳路徑。"""
    路徑 = tmp_path / ".gemini" / "settings.json"
    路徑.parent.mkdir(parents=True, exist_ok=True)
    路徑.write_text(內容, encoding="utf-8")
    return 路徑


def test_gemini_檔案不存在時直接建立(tmp_path):
    路徑 = tmp_path / ".gemini" / "settings.json"
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器()))
    assert 結果.ok is True
    assert json.loads(路徑.read_text(encoding="utf-8"))["mcpServers"]["redmine"] == {
        "command": "redmine-mcp"
    }


def test_gemini_保留其餘_server(tmp_path):
    路徑 = _寫設定(
        tmp_path, json.dumps({"mcpServers": {"serena": {"command": "uv"}}, "ide": {}})
    )
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器()))
    assert 結果.ok is True
    資料 = json.loads(路徑.read_text(encoding="utf-8"))
    assert 資料["mcpServers"]["serena"] == {"command": "uv"}
    assert "ide" in 資料


def test_gemini_同名且內容不同時要問過才覆蓋(tmp_path):
    路徑 = _寫設定(tmp_path, json.dumps({"mcpServers": {"redmine": {"command": "舊的"}}}))
    from tests.test_setup_steps import 靜默提問

    prompts, _said = 靜默提問(confirms=[True])
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器(), prompts=prompts))
    assert 結果.ok is True
    assert json.loads(路徑.read_text(encoding="utf-8"))["mcpServers"]["redmine"] == {
        "command": "redmine-mcp"
    }


def test_gemini_答否時位元組完全不變(tmp_path):
    原文 = json.dumps({"mcpServers": {"redmine": {"command": "舊的"}}})
    路徑 = _寫設定(tmp_path, 原文)
    from tests.test_setup_steps import 靜默提問

    prompts, _ = 靜默提問(confirms=[False])
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器(), prompts=prompts))
    assert 結果.ok is True and 結果.skipped is True
    assert 路徑.read_text(encoding="utf-8") == 原文


def test_gemini_同名但帶額外欄位時仍要問過才覆蓋(tmp_path):
    # command 相同，但既有還帶著 args／env——若只比對 command 會判斷成「沒變」而
    # 靜默覆蓋，使用者自訂的啟動參數與環境變數會整包消失。
    路徑 = _寫設定(
        tmp_path,
        json.dumps(
            {
                "mcpServers": {
                    "redmine": {
                        "command": "redmine-mcp",
                        "args": ["--site", "main"],
                        "trust": True,
                    }
                }
            }
        ),
    )
    from tests.test_setup_steps import 靜默提問

    prompts, _said = 靜默提問(confirms=[True])
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器(), prompts=prompts))
    assert 結果.ok is True
    資料 = json.loads(路徑.read_text(encoding="utf-8"))
    assert 資料["mcpServers"]["redmine"] == {"command": "redmine-mcp"}


def test_gemini_同名帶額外欄位且答否時位元組完全不變(tmp_path):
    原文 = json.dumps(
        {
            "mcpServers": {
                "redmine": {
                    "command": "redmine-mcp",
                    "args": ["--site", "main"],
                    "trust": True,
                }
            }
        }
    )
    路徑 = _寫設定(tmp_path, 原文)
    from tests.test_setup_steps import 靜默提問

    prompts, _ = 靜默提問(confirms=[False])
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器(), prompts=prompts))
    assert 結果.ok is True and 結果.skipped is True
    assert 路徑.read_text(encoding="utf-8") == 原文


def test_gemini_遠端型_url_設定也要問過才覆蓋(tmp_path):
    # 既有是 {"url": ...} 這種遠端型設定，沒有 command 欄位；不可被誤判成
    # 「本來就沒有這一筆」而直接換掉。
    路徑 = _寫設定(
        tmp_path, json.dumps({"mcpServers": {"redmine": {"url": "https://internal/mcp"}}})
    )
    from tests.test_setup_steps import 靜默提問

    被問到: list[str] = []
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=lambda label: (被問到.append(label), True)[1],
        choose=prompts.choose,
        choose_many=prompts.choose_many,
        open_url=prompts.open_url,
    )
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器(), prompts=prompts))
    assert 結果.ok is True
    assert 被問到, "遠端型既有設定也必須問過才可以覆蓋"
    assert json.loads(路徑.read_text(encoding="utf-8"))["mcpServers"]["redmine"] == {
        "command": "redmine-mcp"
    }


def test_gemini_遠端型_url_設定的已註冊判斷為_true(tmp_path):
    路徑 = _寫設定(
        tmp_path, json.dumps({"mcpServers": {"redmine": {"url": "https://internal/mcp"}}})
    )
    assert GeminiCLI註冊器(路徑).已註冊(建立情境(假執行器())) is True


def test_gemini_設定檔非_utf8_編碼時已註冊不拋例外且不註冊(tmp_path):
    路徑 = tmp_path / ".gemini" / "settings.json"
    路徑.parent.mkdir(parents=True, exist_ok=True)
    內容 = json.dumps({"說明": "測試中文", "mcpServers": {}}, ensure_ascii=False)
    路徑.write_bytes(內容.encode("cp950"))

    assert GeminiCLI註冊器(路徑).已註冊(建立情境(假執行器())) is False

    原始位元組 = 路徑.read_bytes()
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器()))
    assert 結果.ok is False
    assert 路徑.read_bytes() == 原始位元組


def test_gemini_同名但內容相同時不必問(tmp_path):
    from tests.test_setup_steps import 靜默提問

    路徑 = _寫設定(
        tmp_path, json.dumps({"mcpServers": {"redmine": {"command": "redmine-mcp"}}})
    )
    被問到: list[str] = []
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=lambda label: (被問到.append(label), True)[1],
        choose=prompts.choose,
        choose_many=prompts.choose_many,
        open_url=prompts.open_url,
    )
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器(), prompts=prompts))
    assert 結果.ok is True
    assert 被問到 == []


def test_gemini_bundle_模式也要真的問(tmp_path):
    # 蓋掉使用者既有的設定不該被自動代答，即使是一鍵安裝模式。
    from tests.test_setup_steps import 靜默提問

    路徑 = _寫設定(tmp_path, json.dumps({"mcpServers": {"redmine": {"command": "舊的"}}}))
    被問到: list[str] = []
    prompts, _ = 靜默提問()
    prompts = prompts.__class__(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=lambda label: (被問到.append(label), False)[1],
        choose=prompts.choose,
        choose_many=prompts.choose_many,
        open_url=prompts.open_url,
    )
    ctx = 建立情境(假執行器(), prompts=prompts, bundle=tmp_path)
    GeminiCLI註冊器(路徑).註冊(ctx)
    assert 被問到, "bundle 模式仍必須問過才可以覆蓋"


def test_gemini_json_壞掉時不寫檔且印手動步驟(tmp_path):
    from tests.test_setup_steps import 靜默提問

    原文 = "{ 這不是 json"
    路徑 = _寫設定(tmp_path, 原文)
    prompts, said = 靜默提問()
    結果 = GeminiCLI註冊器(路徑).註冊(建立情境(假執行器(), prompts=prompts))
    assert 結果.ok is False
    assert 路徑.read_text(encoding="utf-8") == 原文
    assert any("mcpServers" in 行 for 行 in said), "要印出可以自己貼的設定"


def test_gemini_已註冊的判斷(tmp_path):
    路徑 = _寫設定(
        tmp_path, json.dumps({"mcpServers": {"redmine": {"command": "redmine-mcp"}}})
    )
    assert GeminiCLI註冊器(路徑).已註冊(建立情境(假執行器())) is True
    空的 = _寫設定(tmp_path, json.dumps({"mcpServers": {}}))
    assert GeminiCLI註冊器(空的).已註冊(建立情境(假執行器())) is False


def test_gemini_移除只刪那一鍵(tmp_path):
    路徑 = _寫設定(
        tmp_path,
        json.dumps({"mcpServers": {"redmine": {"command": "x"}, "serena": {"command": "y"}}}),
    )
    結果 = GeminiCLI註冊器(路徑).移除(建立情境(假執行器(), mode="uninstall"))
    assert 結果.ok is True
    資料 = json.loads(路徑.read_text(encoding="utf-8"))
    assert "redmine" not in 資料["mcpServers"]
    assert 資料["mcpServers"]["serena"] == {"command": "y"}


def test_gemini_移除時檔案不存在不算失敗(tmp_path):
    結果 = GeminiCLI註冊器(tmp_path / "沒有").移除(建立情境(假執行器(), mode="uninstall"))
    assert 結果.ok is True


def test_登錄表認得兩家():
    assert 取得註冊器("claude-code").__class__ is ClaudeCode註冊器
    assert 取得註冊器("gemini-cli").__class__ is GeminiCLI註冊器


def test_不認得的_harness_一律走手動():
    註冊器 = 取得註冊器("opencode")
    assert 註冊器.__class__ is 手動註冊器
    assert 註冊器.id == "opencode"
    assert "只會印出手動步驟" in 註冊器.說明(建立情境(假執行器()))


def test_手動註冊器不碰任何東西只印步驟():
    from tests.test_setup_steps import 靜默提問

    prompts, said = 靜默提問()
    run = 假執行器()
    結果 = 手動註冊器("opencode").註冊(建立情境(run, prompts=prompts))
    assert 結果.ok is True and 結果.skipped is True
    assert run.calls == []
    assert any("redmine-mcp" in 行 for 行 in said)


def test_手動註冊器的已註冊一律回_false():
    # 我們無從得知使用者有沒有自己貼進去，回 False 讓清單照樣列出來。
    assert 手動註冊器("opencode").已註冊(建立情境(假執行器())) is False


def test_手動註冊器的移除只印步驟():
    from tests.test_setup_steps import 靜默提問

    prompts, said = 靜默提問()
    結果 = 手動註冊器("opencode").移除(建立情境(假執行器(), prompts=prompts, mode="uninstall"))
    assert 結果.ok is True and 結果.skipped is True
    assert said


def test_claude_已註冊時不重跑_add():
    """真實案例：`claude mcp add` 對已存在的名稱會直接失敗。

    紀錄檔實測輸出：`MCP server redmine already exists in user config`，於是清單
    每勾一次 claude-code 就紅一次，還叫使用者「請自行執行」那條他其實已經執行過的
    指令。舊流程靠 check() 擋（已註冊就不進 fix()），改成清單之後每個勾中的工具
    都會無條件跑一次註冊，這個保護就沒了——`移除()` 有先判斷「本來就沒有」，
    `註冊()` 也要對稱地先判斷「本來就有」。
    """
    run = 假執行器(
        CommandResult(0, "2.1.4 (Claude Code)\n", ""),        # claude --version
        CommandResult(0, "redmine:\n  Type: stdio\n", ""),   # claude mcp get（已註冊）
    )
    結果 = ClaudeCode註冊器().註冊(建立情境(run))
    assert 結果.ok is True
    assert 結果.skipped is True
    assert not any(argv[:3] == ["claude", "mcp", "add"] for argv in run.calls), (
        "已註冊還去跑 add 只會拿到 already exists 的失敗"
    )


def test_註冊器要表明自己會不會動到既有檔案():
    """預設勾選規則靠這個旗標決定，少一個實作就會被預設勾進去。"""
    assert ClaudeCode註冊器().會動到既有檔案 is False, "走 CLI，不碰任何既有檔案"
    assert GeminiCLI註冊器().會動到既有檔案 is True, "直接改 ~/.gemini/settings.json"
    assert 手動註冊器("opencode").會動到既有檔案 is False, "只印步驟，什麼都不碰"
