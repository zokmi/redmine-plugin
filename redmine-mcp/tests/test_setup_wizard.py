"""安裝精靈互動流程的測試：以假 Prompts／假 CommandRunner 逐分支驗證。"""
import pytest

from redmine_mcp.errors import RedmineError
from redmine_mcp.setup.external import CommandResult
from redmine_mcp.setup.wizard import (
    Prompts,
    _ask_credentials,
    _ask_site_name,
    _do_register,
    _finish,
    _parse_multi_select,
    run_setup,
    validate_site_name,
)


class 腳本化提問:
    """依序回答預先排好的答案，並記錄所有輸出與每次提問的字面。

    每次 ask／ask_secret／confirm／choose 被呼叫時，除了消耗對應清單的下一個值，
    也會把當次的 label 記進 `labels`；測試結尾斷言 answers／confirms／choices 都已
    耗盡（見 I-2），確保餵進去的資料與流程實際消耗的次數完全一致——`pop(0)` 只在
    餵不夠時才爆，多餵完全無感，靠它自己是不夠的。
    """

    def __init__(self, answers: list, confirms: list[bool], choices: list[int]) -> None:
        self.answers = list(answers)
        self.confirms = list(confirms)
        self.choices = list(choices)
        self.said: list[str] = []
        self.labels: list[str] = []
        self.opened: list[str] = []

    def _panel(self, title: str, lines) -> None:
        # 面板內容也要進 said：金鑰遮罩的斷言必須看得到預覽區塊。
        self.said.append(title)
        self.said.extend(lines)

    def _ask(self, label: str, default: str | None = None) -> str:
        self.labels.append(label)
        return self.answers.pop(0)

    def _ask_secret(self, label: str) -> str:
        self.labels.append(label)
        return self.answers.pop(0)

    def _confirm(self, label: str) -> bool:
        self.labels.append(label)
        return self.confirms.pop(0)

    def _choose(self, label: str, options) -> int:
        self.labels.append(label)
        return self.choices.pop(0)

    def _choose_many(self, label: str, options, default) -> list[int]:
        self.labels.append(label)
        return list(default)

    def build(self) -> Prompts:
        return Prompts(
            say=self.said.append,
            panel=self._panel,
            ask=self._ask,
            ask_secret=self._ask_secret,
            confirm=self._confirm,
            choose=self._choose,
            choose_many=self._choose_many,
            open_url=self.opened.append,
        )

    def assert_exhausted(self) -> None:
        """斷言 answers／confirms／choices 都已用罄，證明流程剛好吃完餵的資料。"""
        assert self.answers == []
        assert self.confirms == []
        assert self.choices == []


async def _成功驗證(url: str, api_key: str) -> str:
    return "kenny"


async def _失敗驗證(url: str, api_key: str) -> str:
    raise RedmineError("金鑰不對")


def _固定回應(code: int, stdout: str = "", stderr: str = ""):
    """建一個對任何 argv 都回同一個結果的假 CommandRunner。"""

    def run(argv, stdin=None):
        return CommandResult(code, stdout, stderr)

    return run


def test_全新安裝寫出參數檔並註冊(tmp_path):
    target = tmp_path / "config.toml"
    # 問憑證提前到問代號之前（推導代號需要網址），因此答案順序是網址、金鑰、代號。
    script = 腳本化提問(
        answers=["https://a/redmine", "secret-key-1234", "main", "", "", ""],
        confirms=[True, True],  # 確認寫入、確認註冊（位置確認已移除，不再問）
        choices=[],
    )
    calls: list[list[str]] = []

    def run(argv, stdin=None):
        calls.append(argv)
        if argv[:3] == ["claude", "mcp", "get"]:
            return CommandResult(1, "", "not found")
        if argv[0] == "redmine-mcp":
            return CommandResult(0, "", "INFO Redmine MCP server 啟動，站台：main\n")
        return CommandResult(0, "", "")

    code = run_setup(script.build(), config_path=target, run=run, verify=_成功驗證)

    assert code == 0
    content = target.read_text(encoding="utf-8")
    assert "[sites.main]" in content
    assert 'api_key     = "secret-key-1234"' in content
    assert ["claude", "mcp", "add", "redmine", "--scope", "user", "--", "redmine-mcp"] in calls
    script.assert_exhausted()


def test_金鑰只以末四碼回顯(tmp_path):
    target = tmp_path / "config.toml"
    # 假 run 對所有呼叫一律回 code 1，因此 _do_register() 在檢查 `claude --version`
    # 那一步就會提早 return，不會再消耗任何 confirm；位置確認已移除，confirms 只需
    # 覆蓋「確認寫入」一題。
    script = 腳本化提問(
        answers=["https://a/redmine", "secret-key-1234", "main", "", "", ""],
        confirms=[True],
        choices=[],
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    # 流程雖然跳過了註冊（找不到 claude CLI）與冒煙測試（同樣的假 run 回應失敗），
    # 但參數檔已經寫入，整體仍視為成功。
    assert code == 0
    # said 同時收集 say() 與 panel() 的內容，因此預覽面板裡的金鑰也在檢查範圍內。
    全部輸出 = "\n".join(script.said)
    assert "secret-key-1234" not in 全部輸出
    assert "****1234" in 全部輸出
    script.assert_exhausted()


def test_不確認寫入就不建立檔案(tmp_path):
    target = tmp_path / "config.toml"
    script = 腳本化提問(
        answers=["https://a/redmine", "secret-key-1234", "main", "", "", ""],
        confirms=[False],  # 位置確認已移除；剩下的唯一一題是「確認寫入嗎？」
        choices=[],
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(0),
        verify=_成功驗證,
    )
    assert code == 1
    assert not target.exists()
    script.assert_exhausted()


def test_站台代號規則沿用_config_的驗證():
    assert validate_site_name("main") is None
    assert validate_site_name("中文站") is not None
    assert validate_site_name("-bad") is not None
    assert validate_site_name("") is not None


def test_取代既有站台不影響其他站台(tmp_path):
    """取代分支現在會先用既有金鑰驗一次（`_成功驗證` 對任何金鑰都放行）並問要不要
    換一把新的；這裡答「要換」才會走到 `ask_secret`，因此比改動前多消耗一個
    confirm——這正是本次「既有設定要能沿用」帶來的行為差異，不是測試寫錯。
    """
    target = tmp_path / "config.toml"
    target.write_text(
        '[sites.a]\nurl = "https://a/x"\napi_key = "old"\n\n'
        '[sites.b]\nurl = "https://b/x"\napi_key = "keep"\n',
        encoding="utf-8",
    )
    # 既有檔案不再詢問下載／上傳目錄（I-1），因此只需網址、金鑰、說明三個答案。
    script = 腳本化提問(
        answers=["https://a/new", "new-key-9999", ""],
        confirms=[True, True],  # 要換一把新的金鑰嗎？（要換）、確認寫入嗎？
        choices=[1, 0],  # 選「取代其中一個站台」→ 選 a
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    content = target.read_text(encoding="utf-8")
    assert code == 0
    assert 'api_key     = "new-key-9999"' in content
    assert 'api_key = "keep"' in content
    # 舊金鑰必須真的被替換掉，不是退化成「附加而非替換」。
    assert '"old"' not in content
    script.assert_exhausted()


def test_取代既有站台且沿用原金鑰時不呼叫_ask_secret(tmp_path):
    """核心驗收：既有金鑰驗得過、使用者答「不換」時，完全不觸碰 ask_secret，
    寫回參數檔的金鑰與原本相同。
    """
    target = tmp_path / "config.toml"
    target.write_text(
        '[sites.a]\nurl = "https://a/x"\napi_key = "old-key-1234"\n', encoding="utf-8"
    )
    ask_secret_呼叫次數 = {"count": 0}

    def _不該被呼叫的_ask_secret(label: str) -> str:
        ask_secret_呼叫次數["count"] += 1
        return "不該被用到的值"

    script = 腳本化提問(
        answers=["https://a/x", ""],  # 網址直接沿用現值、站台說明留白
        confirms=[False, True],  # 要換一把新的金鑰嗎？（不換）、確認寫入嗎？
        choices=[1, 0],  # 取代其中一個站台 → 選 a
    )
    prompts = script.build()
    prompts = Prompts(
        say=prompts.say,
        panel=prompts.panel,
        ask=prompts.ask,
        ask_secret=_不該被呼叫的_ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=prompts.choose_many,
        open_url=prompts.open_url,
    )
    code = run_setup(
        prompts,
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    content = target.read_text(encoding="utf-8")
    assert code == 0
    assert ask_secret_呼叫次數["count"] == 0, "答『不換』時不該呼叫 ask_secret"
    assert 'api_key     = "old-key-1234"' in content
    script.assert_exhausted()


def test_取代既有站台且金鑰驗不過時直接要求輸入新金鑰不問要不要換(tmp_path):
    """既有金鑰已經失效時，不該再多問一句「要換嗎」——答案顯然是要換。"""
    target = tmp_path / "config.toml"
    target.write_text(
        '[sites.a]\nurl = "https://a/x"\napi_key = "expired-key"\n', encoding="utf-8"
    )

    async def _舊金鑰失效(url: str, api_key: str) -> str:
        if api_key == "expired-key":
            raise RedmineError("401 認證失敗")
        return "kenny"

    script = 腳本化提問(
        answers=["https://a/x", "brand-new-key", ""],
        confirms=[True],  # 只剩「確認寫入嗎？」；不該有「要換一把新的金鑰嗎？」
        choices=[1, 0],  # 取代其中一個站台 → 選 a
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_舊金鑰失效,
    )
    content = target.read_text(encoding="utf-8")
    assert code == 0
    assert 'api_key     = "brand-new-key"' in content
    全部輸出 = "\n".join(script.said)
    assert "要換一把新的金鑰嗎" not in 全部輸出
    assert "驗證失敗" in 全部輸出
    script.assert_exhausted()


def test_取代既有站台時網址帶預設值直接Enter就沿用(tmp_path):
    """網址提問要把現值當預設帶進去；這裡的假 Prompts 對 default 給了值就直接照抄
    （模擬使用者直接按 Enter），驗證沿用的正是現有網址而不是空字串。
    """
    target = tmp_path / "config.toml"
    target.write_text(
        '[sites.a]\nurl = "https://a.example.com/redmine"\napi_key = "old-key-1234"\n',
        encoding="utf-8",
    )
    收到的預設值: list[str | None] = []

    def _沿用預設的_ask(label: str, default: str | None = None) -> str:
        if "網址" in label:
            收到的預設值.append(default)
            return default or ""
        return ""  # 站台說明留白

    def _選擇(label: str, options) -> int:
        if label == "要做什麼？":
            return 1  # 取代其中一個站台
        return 0  # 要取代哪一個？→ 只有一個既有站台，選它

    prompts = Prompts(
        say=lambda text: None,
        panel=lambda title, lines: None,
        ask=_沿用預設的_ask,
        ask_secret=lambda label: "should-not-be-asked",
        confirm=lambda label: False if "換" in label else True,
        choose=_選擇,
        choose_many=lambda label, options, default: list(default),
        open_url=lambda url: None,
    )
    # 只有一個既有站台，choose("要取代哪一個？", taken) 的選項只有一項，索引 0。
    code = run_setup(
        prompts,
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    assert code == 0
    assert 收到的預設值 == ["https://a.example.com/redmine"]
    content = target.read_text(encoding="utf-8")
    assert 'url         = "https://a.example.com/redmine"' in content


def test_舊格式不同意轉換就完全不動檔案(tmp_path):
    target = tmp_path / "config.toml"
    原文 = 'url = "https://a/x"\napi_key = "old"\n'
    target.write_text(原文, encoding="utf-8")
    # 位置確認已移除，只剩「要現在轉換嗎？」這一題，答 False 直接結束。
    script = 腳本化提問(answers=[], confirms=[False], choices=[])
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(0),
        verify=_成功驗證,
    )
    assert code == 1
    assert target.read_text(encoding="utf-8") == 原文
    script.assert_exhausted()


def test_既有全域設定不被覆蓋也不被詢問(tmp_path):
    """I-1：既有參數檔的 download_dir／upload_dir 不再被詢問，也不會被丟掉。"""
    target = tmp_path / "config.toml"
    原文 = (
        'download_dir = "/keep/download"\n'
        'upload_dir   = "/keep/upload"\n'
        "\n"
        '[sites.a]\n'
        'url = "https://a/x"\n'
        'api_key = "old"\n'
    )
    target.write_text(原文, encoding="utf-8")
    # 問憑證提前到問代號之前，因此答案順序是網址、金鑰、代號、說明。
    script = 腳本化提問(
        answers=["https://b/x", "new-key", "new", ""],
        confirms=[True],  # 位置確認已移除，只剩「確認寫入嗎？」
        choices=[0],  # 選「新增一個站台」
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    content = target.read_text(encoding="utf-8")
    assert code == 0
    assert '"/keep/download"' in content
    assert '"/keep/upload"' in content
    assert "[sites.a]" in content
    assert "[sites.new]" in content
    assert 'api_key     = "new-key"' in content
    全部輸出 = "\n".join(script.said)
    assert "既有參數檔的全域設定" in 全部輸出
    script.assert_exhausted()


def test_既有站台缺必填欄位時訊息不可斷言是工具bug(tmp_path):
    """I-4：既有參數檔本身就不完整（語法合法、但缺 api_key）時，新增另一個站台會在
    寫檔前的驗證閘被擋下——這不是本次新增站台這個操作造成的，訊息不可誤導使用者
    以為是工具本身壞了、要他們去回報，而要講清楚「先去補齊既有那一處」。
    """
    target = tmp_path / "config.toml"
    原文 = '[sites.old]\nurl = "https://old/x"\n'
    target.write_text(原文, encoding="utf-8")
    # 問憑證提前到問代號之前，因此答案順序是網址、金鑰、代號、說明。
    script = 腳本化提問(
        answers=["https://new/x", "new-key-9999", "new", ""],
        # 驗證閘會在「確認寫入嗎？」之前就擋下，而位置確認已移除，因此完全不會
        # 消耗任何 confirm；若這裡誤放一個，script.assert_exhausted() 會抓到多餘的一個。
        confirms=[],
        choices=[0],  # 選「新增一個站台」
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )

    assert code == 1
    # 參數檔未被修改：既沒有寫入新站台，也沒有動到既有的 old 區塊。
    assert target.read_text(encoding="utf-8") == 原文
    全部輸出 = "\n".join(script.said)
    assert "api_key" in 全部輸出
    assert "手動補齊" in 全部輸出
    # 不可讓使用者誤以為這是工具本身的問題而去回報；本工具只在「確認原本的參數檔
    # 沒問題」的前提下才是工具的錯，這裡原本的參數檔就有問題。
    assert "工具的問題，請回報" not in 全部輸出
    script.assert_exhausted()


def test_壞_TOML_直接結束而不寫檔(tmp_path):
    """I-3：classify() 拋 ConfigError 時要有錯誤訊息並結束，不留下 traceback。"""
    target = tmp_path / "config.toml"
    壞內容 = "url = \n"
    target.write_text(壞內容, encoding="utf-8")
    # 位置確認已移除；classify() 在任何 confirm 之前就會拋出，因此不消耗任何 confirm。
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(0),
        verify=_成功驗證,
    )
    assert code == 1
    assert target.read_text(encoding="utf-8") == 壞內容
    script.assert_exhausted()


def test_sites分支選擇取消(tmp_path):
    """I-3：sites 分支的「取消」選項要直接結束，不動檔案。"""
    target = tmp_path / "config.toml"
    原文 = '[sites.a]\nurl = "https://a/x"\napi_key = "old"\n'
    target.write_text(原文, encoding="utf-8")
    # 位置確認已移除；選「取消」發生在任何 confirm 之前，因此不消耗任何 confirm。
    script = 腳本化提問(answers=[], confirms=[], choices=[2])
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(0),
        verify=_成功驗證,
    )
    assert code == 1
    assert target.read_text(encoding="utf-8") == 原文
    script.assert_exhausted()


def test_legacy同意轉換後完成安裝(tmp_path):
    """I-3：legacy 同意轉換的成功路徑，轉換後的站台與新加站台都要在檔案裡。"""
    target = tmp_path / "config.toml"
    target.write_text('url = "https://old/x"\napi_key = "oldkey"\n', encoding="utf-8")
    # 問憑證提前到問代號之前，因此答案順序是網址、金鑰、代號、說明。
    script = 腳本化提問(
        answers=["https://new/x", "newkey", "main", ""],
        confirms=[True, True],  # 同意轉換、確認寫入（位置確認已移除）
        choices=[0],  # 轉換後選「新增一個站台」
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    content = target.read_text(encoding="utf-8")
    assert code == 0
    assert "[sites.default]" in content
    assert "[sites.main]" in content
    assert 'api_key     = "newkey"' in content
    script.assert_exhausted()


def test_權限收緊失敗會提示使用者(tmp_path):
    """I-3：tighten_permissions() 失敗時要印出警告，不能默默吞掉。"""
    target = tmp_path / "config.toml"

    def run(argv, stdin=None):
        if argv and argv[0] == "icacls":
            return CommandResult(1, "", "拒絕存取")
        return CommandResult(1, "", "")

    script = 腳本化提問(
        answers=["https://a/x", "secret1234", "main", "", "", ""],
        confirms=[True],  # 位置確認已移除，只剩「確認寫入嗎？」
        choices=[],
    )
    code = run_setup(script.build(), config_path=target, run=run, verify=_成功驗證)
    assert code == 0
    全部輸出 = "\n".join(script.said)
    assert "權限收緊失敗" in 全部輸出
    script.assert_exhausted()


def test_取代非標準寫法的站台會回報錯誤而不是丟例外(tmp_path):
    """I-4：upsert_site_block() 的 ConfigError 要被接住並回報，不能變成 traceback。

    取代分支現在會先用既有金鑰驗一次並問要不要換（`_成功驗證` 對任何金鑰都放行），
    這裡答「要換」才會進到 `ask_secret`；ConfigError 仍在「確認寫入嗎？」之前就會
    被拋出並接住，因此除了這一題之外不再消耗其他 confirm。
    """
    target = tmp_path / "config.toml"
    原文 = '["sites"."main"]\nurl = "https://a/x"\napi_key = "old"\n'
    target.write_text(原文, encoding="utf-8")
    script = 腳本化提問(
        answers=["https://new/x", "newkey", ""],
        confirms=[True],  # 要換一把新的金鑰嗎？（要換）
        choices=[1, 0],  # 取代其中一個站台 → 選 main
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    assert code == 1
    assert target.read_text(encoding="utf-8") == 原文
    全部輸出 = "\n".join(script.said)
    assert "無法安全改寫" in 全部輸出
    script.assert_exhausted()


def test_寫入失敗會回報而不是丟出例外(tmp_path, monkeypatch):
    """I-4：write_config() 的 OSError 要被接住並回報，不能變成 traceback。"""
    import redmine_mcp.setup.wizard as wizard模組

    def _炸掉(path, content):
        raise OSError("唯讀檔案系統")

    monkeypatch.setattr(wizard模組, "write_config", _炸掉)

    target = tmp_path / "config.toml"
    script = 腳本化提問(
        answers=["https://a/x", "secret1234", "main", "", "", ""],
        confirms=[True],  # 位置確認已移除，只剩「確認寫入嗎？」
        choices=[],
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=_固定回應(1),
        verify=_成功驗證,
    )
    assert code == 1
    assert not target.exists()
    全部輸出 = "\n".join(script.said)
    assert "寫入失敗" in 全部輸出
    script.assert_exhausted()


# --- _do_register() 的分支 -------------------------------------------------


def test_註冊_已註冊選擇覆蓋():
    calls: list[list[str]] = []

    def run(argv, stdin=None):
        calls.append(argv)
        if argv[:3] == ["claude", "mcp", "get"]:
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")

    script = 腳本化提問(answers=[], confirms=[True, True], choices=[])
    描述 = _do_register(script.build(), run)

    assert ["claude", "mcp", "remove", "redmine", "--scope", "user"] in calls
    assert ["claude", "mcp", "add", "redmine", "--scope", "user", "--", "redmine-mcp"] in calls
    assert "已註冊" in "\n".join(script.said)
    assert 描述 == "註冊 scope：user（所有專案都吃得到）"
    script.assert_exhausted()


def test_註冊_已註冊選擇保留():
    calls: list[list[str]] = []

    def run(argv, stdin=None):
        calls.append(argv)
        if argv[:3] == ["claude", "mcp", "get"]:
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")

    script = 腳本化提問(answers=[], confirms=[False], choices=[])
    描述 = _do_register(script.build(), run)

    assert not any(argv[:3] == ["claude", "mcp", "remove"] for argv in calls)
    assert not any(argv[:3] == ["claude", "mcp", "add"] for argv in calls)
    assert "保留原有註冊" in "\n".join(script.said)
    # 沿用原有註冊也不能報成「本次已註冊」：使用者可能正想換掉舊的執行檔路徑。
    assert 描述 == "註冊：沿用原有設定（本次未變更）"
    script.assert_exhausted()


def test_註冊_使用者拒絕註冊():
    calls: list[list[str]] = []

    def run(argv, stdin=None):
        calls.append(argv)
        if argv[:3] == ["claude", "mcp", "get"]:
            return CommandResult(1, "", "not found")
        return CommandResult(0, "", "")

    script = 腳本化提問(answers=[], confirms=[False], choices=[])
    描述 = _do_register(script.build(), run)

    assert not any(argv[:3] == ["claude", "mcp", "add"] for argv in calls)
    全部輸出 = "\n".join(script.said)
    assert "跳過註冊" in 全部輸出
    assert "claude mcp add redmine --scope user -- redmine-mcp" in 全部輸出
    assert "未完成" in 描述
    script.assert_exhausted()


def test_註冊失敗印出訊息():
    def run(argv, stdin=None):
        if argv[:3] == ["claude", "mcp", "get"]:
            return CommandResult(1, "", "not found")
        if argv[:3] == ["claude", "mcp", "add"]:
            return CommandResult(1, "", "權限不足")
        return CommandResult(0, "", "")

    script = 腳本化提問(answers=[], confirms=[True], choices=[])
    描述 = _do_register(script.build(), run)

    全部輸出 = "\n".join(script.said)
    assert "註冊失敗" in 全部輸出
    assert "權限不足" in 全部輸出
    assert "未完成" in 描述
    script.assert_exhausted()


def test_找不到_claude_CLI():
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    描述 = _do_register(script.build(), _固定回應(127, "", "找不到執行檔"))

    assert "找不到 claude CLI" in "\n".join(script.said)
    assert "未完成" in 描述
    script.assert_exhausted()


# --- _finish() 的分支 -------------------------------------------------------


def test_冒煙測試失敗印出排錯提示(tmp_path):
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    _finish(
        script.build(),
        tmp_path / "config.toml",
        "main",
        _固定回應(1, "", "boom"),
        "註冊 scope：user（所有專案都吃得到）",
    )

    全部輸出 = "\n".join(script.said)
    assert "server 啟動測試沒過" in 全部輸出
    assert "boom" in 全部輸出
    script.assert_exhausted()


def test_總結原樣印出註冊結果描述(tmp_path):
    """總結不可無條件宣稱已註冊：跳過或失敗時必須讓使用者在同一個面板看到。"""
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    _finish(
        script.build(),
        tmp_path / "config.toml",
        "main",
        _固定回應(0, "", "INFO Redmine MCP server 啟動，站台：main\n"),
        "註冊：**未完成**，請自行執行 claude mcp add redmine --scope user -- redmine-mcp",
    )

    全部輸出 = "\n".join(script.said)
    assert "未完成" in 全部輸出
    assert "註冊 scope：user" not in 全部輸出
    script.assert_exhausted()


# --- _ask_credentials() 的分支 ----------------------------------------------


def test_驗證失敗可重試後成功():
    嘗試次數 = {"count": 0}

    async def 先失敗再成功(url: str, api_key: str) -> str:
        嘗試次數["count"] += 1
        if 嘗試次數["count"] == 1:
            raise RedmineError("金鑰不對")
        return "kenny"

    script = 腳本化提問(
        answers=["https://a/x", "bad-key", "https://a/x", "good-key"],
        confirms=[True],
        choices=[],
    )
    result = _ask_credentials(script.build(), 先失敗再成功)

    assert result == ("https://a/x", "good-key")
    全部輸出 = "\n".join(script.said)
    assert "驗證失敗" in 全部輸出
    assert "金鑰不對" in 全部輸出
    script.assert_exhausted()


def test_驗證失敗且放棄重試():
    script = 腳本化提問(answers=["https://a/x", "bad-key"], confirms=[False], choices=[])
    result = _ask_credentials(script.build(), _失敗驗證)

    assert result is None
    script.assert_exhausted()


def test_網址或金鑰留白且放棄重填():
    # 網址與金鑰的留白檢查現在分兩段做（網址一留白就立刻詢問要不要重試，
    # 不會多問一次金鑰）：這裡只留白網址一項即可觸發放棄重試的分支。
    script = 腳本化提問(answers=[""], confirms=[False], choices=[])
    result = _ask_credentials(script.build(), _成功驗證)

    assert result is None
    script.assert_exhausted()


def test_網址留白重新輸入後成功():
    script = 腳本化提問(
        answers=["", "https://a/x", "good-key"],
        confirms=[True],
        choices=[],
    )
    result = _ask_credentials(script.build(), _成功驗證)

    assert result == ("https://a/x", "good-key")
    script.assert_exhausted()


def test_金鑰留白且放棄重填():
    script = 腳本化提問(answers=["https://a/x", ""], confirms=[False], choices=[])
    result = _ask_credentials(script.build(), _成功驗證)

    assert result is None
    script.assert_exhausted()


# --- _ask_site_name() 的分支 -------------------------------------------------


def test_代號不合法可重試後成功():
    script = 腳本化提問(answers=["中文站", "main"], confirms=[], choices=[])
    result = _ask_site_name(script.build(), [])

    assert result == "main"
    script.assert_exhausted()


def test_代號重複可重試後成功():
    script = 腳本化提問(answers=["a", "b"], confirms=[], choices=[])
    result = _ask_site_name(script.build(), ["a"])

    assert result == "b"
    script.assert_exhausted()


def test_連續五次失敗回傳None():
    script = 腳本化提問(answers=["中文站"] * 5, confirms=[], choices=[])
    result = _ask_site_name(script.build(), [])

    assert result is None
    script.assert_exhausted()


# --- console_prompts() 的終端相容性 -------------------------------------------


def test_字碼頁編不出的記號不會讓輸出崩掉(monkeypatch):
    """實測踩過的坑：在字碼頁 950 的終端上，Rich 印 `✓` 會拋 UnicodeEncodeError。

    整個安裝流程會因為一個裝飾性記號而中斷（檔案其實已經寫好了），使用者只看到一坨
    traceback。這裡用 cp950 的輸出流重現，斷言 say() 不再拋出——寧可那個記號退化成 `?`
    也不能讓流程炸掉。
    """
    import io
    import sys

    from redmine_mcp.setup.wizard import console_prompts

    緩衝 = io.BytesIO()
    假stdout = io.TextIOWrapper(緩衝, encoding="cp950", newline="")
    monkeypatch.setattr(sys, "stdout", 假stdout)

    prompts = console_prompts()
    prompts.say("[green]✓[/green] 已寫入 config.toml")
    prompts.panel("完成", ["站台代號：main"])

    假stdout.flush()
    輸出 = 緩衝.getvalue().decode("cp950", errors="replace")
    # 中文本來就編得出來，必須原樣保留；崩掉的只會是那個記號。
    assert "已寫入" in 輸出


def test_有紀錄檔時輸出會同步追加成純文字(tmp_path):
    """一鍵安裝的紀錄檔不能只靠 PowerShell 的 Start-Transcript。

    PowerShell 5.1 抓 native 子行程的輸出是從主控台緩衝區讀的，雙寬字元佔兩格
    會被讀成兩次，紀錄檔裡就變成「安安裝裝」——那份檔案是要傳給 IT 看的，糊掉
    等於沒有。改由這一層自己把每一行追加進同一個檔案，內容才是乾淨的 UTF-8。
    """
    from redmine_mcp.setup.wizard import console_prompts

    紀錄檔 = tmp_path / "安裝紀錄.txt"
    prompts = console_prompts(log_path=紀錄檔)
    prompts.say("[green]✓[/green] 安裝 redmine-mcp")
    prompts.panel("完成", ["參數檔：config.toml"])

    內容 = 紀錄檔.read_text(encoding="utf-8")
    assert "✓ 安裝 redmine-mcp" in 內容
    assert "[green]" not in 內容, "紀錄檔要存純文字，不留 Rich 標記"
    assert "完成" in 內容
    assert "參數檔：config.toml" in 內容


def test_沒有紀錄檔時不會寫出任何檔案(tmp_path, monkeypatch):
    from redmine_mcp.setup.wizard import console_prompts

    monkeypatch.chdir(tmp_path)
    console_prompts().say("安裝 redmine-mcp")
    assert list(tmp_path.iterdir()) == []


def test_紀錄檔寫不進去不會讓安裝中斷(tmp_path):
    """紀錄只是輔助，唯讀目錄之類的情況不該讓整個安裝爆在一行 say() 上。"""
    from redmine_mcp.setup.wizard import console_prompts

    prompts = console_prompts(log_path=tmp_path / "不存在的目錄" / "安裝紀錄.txt")
    prompts.say("安裝 redmine-mcp")


# --- bundle_mode：精靈瘦身到 2 題 --------------------------------------------


def _答要修改(label: str) -> bool:
    """既有設定的機器會先被問「這次要修改嗎？」，測試裡一律答「要」。

    其餘 confirm 在 bundle 模式不該出現，出現就讓測試失敗——這是「一鍵安裝不多問」
    的反向保護，不能因為多了這一問就整個放行。
    """
    if "要修改嗎" in label:
        return True
    pytest.fail(f"bundle 模式不該問：{label}")


def _選新增一個站台(label: str, options) -> int:
    """既有站台不只一個時會被問「要修改哪一個站台？」，測試裡一律選最後一項。

    最後一項固定是「新增一個站台」，等同舊行為（不沿用任何一站的現值）。
    """
    if "要修改哪一個站台" in label:
        return len(options) - 1
    pytest.fail(f"bundle 模式不該讓人選：{label}")


def 精簡提問(*, 網址: str, 問過: list[str] | None = None,
             金鑰問過: list[str] | None = None, 開過: list[str] | None = None) -> Prompts:
    """建立一個「網址照給、金鑰固定、其餘一律拒答」的 Prompts。

    參數:
        網址: 使用者會貼的網址。
        問過: 有給時，每次 ask() 的提示詞會附加進去。
        金鑰問過: 有給時，每次 ask_secret() 的提示詞會附加進去。
        開過: 有給時，每次 open_url() 的網址會附加進去。
    回傳:
        可直接餵給 run_setup(bundle_mode=True) 的 Prompts。
    """
    問過 = 問過 if 問過 is not None else []
    金鑰問過 = 金鑰問過 if 金鑰問過 is not None else []
    開過 = 開過 if 開過 is not None else []
    return Prompts(
        say=lambda text: None,
        panel=lambda title, lines: None,
        ask=lambda label, default=None: (問過.append(label), 網址)[1],
        ask_secret=lambda label: (金鑰問過.append(label), "k" * 40)[1],
        confirm=_答要修改,
        choose=_選新增一個站台,
        choose_many=lambda label, options, default: pytest.fail(
            f"bundle 模式不該讓人多選：{label}"
        ),
        open_url=開過.append,
    )


def test_bundle_模式只問兩題(tmp_path):
    """核心驗收條件：`run_setup()` 這一層只問「網址」與「金鑰」兩題——**這個題數
    只在「全新安裝（沒有既有參數檔）」這個情境下成立**，此測試就是釘死這個情境
    （`config_path` 指向一個不存在的檔案）。

    位置確認、站台代號、站台說明、下載／上傳目錄、寫入確認全部不問——多一題就是
    多一個非 IT 同事會停下來問人的地方。

    範圍提醒：這條測試只涵蓋參數收集階段（`run_setup()`），不會經過安裝步驟表
    的 `InstallSkillsStep`。整個 bundle 流程實際會問的第三題（多選要讓哪些
    AI 工具讀得到撰寫格式 skill）在 `InstallSkillsStep.fix()` 裡，見 design.md
    的「驗收後追加：撰寫格式 skill 改成多選 harness（2026-08-29）」——那一節
    才是「使用者共被問幾題」的完整驗收條件，這裡的「兩題」與那邊的「三題」
    並不矛盾，只是分屬不同層級。

    **既有設定情境下題數會不同（2026-08-29 追加，見 design.md 同日期那一節）**：
    若參數檔已存在且只有一個站台，網址提問會帶出現值當預設（直接 Enter 就沿用，
    仍然只問一次），但金鑰若驗證通過，會多問一句「要換一把新的金鑰嗎？」——
    這是本次「流程一致化」刻意要問的一題，不是退化；見
    `test_bundle_模式只有一個既有站台時金鑰驗得過可直接沿用()` 與
    `test_bundle_模式代號已存在時覆蓋該站台且不加後綴不影響其他站台()`。
    """
    問過: list[str] = []
    金鑰問過: list[str] = []
    開過: list[str] = []
    prompts = 精簡提問(
        網址="https://redmine.example.com/redmine",
        問過=問過,
        金鑰問過=金鑰問過,
        開過=開過,
    )
    code = run_setup(
        prompts,
        config_path=tmp_path / "config.toml",
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    assert code == 0
    assert len(問過) == 1, f"只該問網址一題，實際問了：{問過}"
    assert len(金鑰問過) == 1
    assert 開過 == ["https://redmine.example.com/redmine/my/account"]


def test_bundle_模式的代號由網址推導並寫進檔案(tmp_path):
    設定檔 = tmp_path / "config.toml"
    run_setup(
        精簡提問(網址="https://redmine.example.com/redmine"),
        config_path=設定檔,
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    assert "[sites.example]" in 設定檔.read_text(encoding="utf-8")


def test_bundle_模式代號已存在時覆蓋該站台且不加後綴不影響其他站台(tmp_path):
    """裁定：bundle 模式推導出的代號若已存在，代表使用者要覆蓋那一站的設定
    （例如換一把新金鑰），而不是疊出 `example-2` 這種重複站台；其他既有站台
    （這裡是 other）必須原封不動。

    這個情境有兩個既有站台，因此不落入「只有一站時沿用網址／金鑰預設值」那條
    分支（見下一條測試），維持精簡提問() 的「confirm 一定不會被呼叫」假設。
    """
    設定檔 = tmp_path / "config.toml"
    設定檔.write_text(
        '[sites.example]\nurl = "https://old.example.com/redmine"\n'
        'api_key = "old-key"\n\n'
        '[sites.other]\nurl = "https://other.example.com/redmine"\n'
        'api_key = "otherkey"\n',
        encoding="utf-8",
    )
    code = run_setup(
        精簡提問(網址="https://redmine.example.com/redmine"),
        config_path=設定檔,
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    content = 設定檔.read_text(encoding="utf-8")
    assert code == 0
    assert content.count("[sites.example]") == 1
    assert "[sites.example-2]" not in content
    assert "old-key" not in content, "覆蓋後舊金鑰不該還留在檔案裡"
    # 另一個既有站台完全不受影響。
    assert "[sites.other]" in content
    assert 'api_key = "otherkey"' in content


def test_bundle_模式只有一個既有站台時金鑰驗得過可直接沿用(tmp_path):
    """bundle 模式只服務單一站台的非 IT 使用者；既有參數檔只有一個站台時，
    幾乎必然就是這次要覆蓋的那一站，因此金鑰驗得過、使用者答「不換」時應完全
    沿用原金鑰，不必呼叫 ask_secret。

    這裡輸入的網址與既有站台推導出的代號相同（都是 example），因此不該觸發
    「換網址時代號不同」的額外確認——`choose` 一被呼叫就會讓測試失敗，是這個
    分支不該退化成每次都問的反向保護。
    """
    設定檔 = tmp_path / "config.toml"
    設定檔.write_text(
        '[sites.example]\nurl = "https://redmine.example.com/redmine"\n'
        'api_key = "old-key-1234"\n',
        encoding="utf-8",
    )
    ask_secret_呼叫次數 = {"count": 0}

    def _不該被呼叫的_ask_secret(label: str) -> str:
        ask_secret_呼叫次數["count"] += 1
        return "不該被用到的值"

    prompts = Prompts(
        say=lambda text: None,
        panel=lambda title, lines: None,
        ask=lambda label, default=None: default or "https://redmine.example.com/redmine",
        ask_secret=_不該被呼叫的_ask_secret,
        # 第一問是「這次要修改嗎？」→ 要；第二問是「要換一把新的金鑰嗎？」→ 不換。
        confirm=lambda label: "要修改嗎" in label,
        choose=lambda label, options: pytest.fail(f"bundle 模式不該讓人選：{label}"),
        choose_many=lambda label, options, default: pytest.fail(
            f"bundle 模式不該讓人多選：{label}"
        ),
        open_url=lambda url: None,
    )
    code = run_setup(
        prompts,
        config_path=設定檔,
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    content = 設定檔.read_text(encoding="utf-8")
    assert code == 0
    assert ask_secret_呼叫次數["count"] == 0
    assert 'api_key     = "old-key-1234"' in content


def _換網址情境的_prompts(
    新網址: str, *, 選擇: int, 收到的問句: list[str] | None = None
) -> Prompts:
    """建立「唯一既有站台 + 這次網址推導出不同代號」情境用的 Prompts。

    參數:
        新網址: 使用者這次貼的網址。
        選擇: `choose()` 被呼叫時要回傳的索引（0＝取代、1＝新增）。
        收到的問句: 有給時，`choose()` 收到的 label 會附加進去，供斷言內容。
    回傳:
        可直接餵給 `run_setup(bundle_mode=True)` 的 Prompts。
    """
    收到的問句 = 收到的問句 if 收到的問句 is not None else []

    def _choose(label: str, options: object) -> int:
        收到的問句.append(label)
        return 選擇

    return Prompts(
        say=lambda text: None,
        panel=lambda title, lines: None,
        ask=lambda label, default=None: 新網址 if "網址" in label else "",
        ask_secret=lambda label: "brand-new-key",
        # _成功驗證 對任何金鑰都放行，因此舊金鑰在新網址上一樣會驗證通過，
        # 接著會問「要換一把新的金鑰嗎？」——這裡答「要換」，才會走進
        # ask_secret 拿到 brand-new-key，驗證新輸入的內容真的被寫進檔案。
        confirm=lambda label: True,
        choose=_choose,
        choose_many=lambda label, options, default: list(default),
        open_url=lambda url: None,
    )


def test_bundle_模式換網址時代號不同會跳出選擇並可選取代(tmp_path):
    """核心驗收：既有參數檔只有一站，但這次的網址推導出不同代號（換了 Redmine
    網址的情境）——不能靜默新增（留下使用者不知道的殭屍站台），也不能靜默取代
    （可能刪掉他刻意保留的東西），必須明講並讓使用者選。這裡選「取代」。
    """
    設定檔 = tmp_path / "config.toml"
    設定檔.write_text(
        '[sites.main]\nurl = "https://redmine.old.com/redmine"\n'
        'api_key = "old-key-1234"\n',
        encoding="utf-8",
    )
    收到的問句: list[str] = []
    prompts = _換網址情境的_prompts(
        "https://redmine.new-host.com/redmine", 選擇=0, 收到的問句=收到的問句
    )
    code = run_setup(
        prompts,
        config_path=設定檔,
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    content = 設定檔.read_text(encoding="utf-8")
    assert code == 0
    assert len(收到的問句) == 1, "必須真的跳出這個選擇，不能靜默處理"
    assert "main" in 收到的問句[0]
    assert "new-host" in 收到的問句[0]
    # 選「取代」：只剩既有代號 main 這一個站台，內容是新網址與新金鑰。
    assert content.count("[sites.") == 1
    assert "[sites.main]" in content
    assert "[sites.new-host]" not in content
    assert 'url         = "https://redmine.new-host.com/redmine"' in content
    assert 'api_key     = "brand-new-key"' in content
    assert "old-key-1234" not in content


def test_bundle_模式換網址時代號不同會跳出選擇並可選新增(tmp_path):
    """同上情境，這次選「新增」：兩個站台都要保留，原本那站的內容不受影響。"""
    設定檔 = tmp_path / "config.toml"
    設定檔.write_text(
        '[sites.main]\nurl = "https://redmine.old.com/redmine"\n'
        'api_key = "old-key-1234"\n',
        encoding="utf-8",
    )
    prompts = _換網址情境的_prompts("https://redmine.new-host.com/redmine", 選擇=1)
    code = run_setup(
        prompts,
        config_path=設定檔,
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    content = 設定檔.read_text(encoding="utf-8")
    assert code == 0
    assert content.count("[sites.") == 2
    assert "[sites.main]" in content
    assert "[sites.new-host]" in content
    assert 'api_key = "old-key-1234"' in content, "原本那站不該被動到"
    assert 'api_key     = "brand-new-key"' in content


def test_網址少了子路徑時自動補上重試(tmp_path):
    """使用者只貼網域（README FAQ 第一名的錯）時，自動再試一次 /redmine。"""
    嘗試過: list[str] = []

    async def 只有子路徑才通(url: str, api_key: str) -> str:
        嘗試過.append(url)
        if url.endswith("/redmine"):
            return "someone"
        raise RedmineError("回應不是 JSON")

    設定檔 = tmp_path / "config.toml"
    code = run_setup(
        精簡提問(網址="https://example.com"),
        config_path=設定檔,
        run=_固定回應(0),
        verify=只有子路徑才通,
        finish=False,
        bundle_mode=True,
    )
    assert code == 0
    assert 嘗試過 == ["https://example.com", "https://example.com/redmine"]
    assert "https://example.com/redmine" in 設定檔.read_text(encoding="utf-8")


def test_bundle_模式三次都連不上就放棄(tmp_path):
    async def 一律失敗(url: str, api_key: str) -> str:
        raise RedmineError("401")

    code = run_setup(
        精簡提問(網址="https://example.com/redmine"),
        config_path=tmp_path / "config.toml",
        run=_固定回應(0),
        verify=一律失敗,
        finish=False,
        bundle_mode=True,
    )
    assert code == 1
    assert not (tmp_path / "config.toml").exists()


def test_非_bundle_模式維持既有問法(tmp_path):
    """回歸防線：工程師走的既有路徑（含代號提問）不因本次改動而變。"""
    問過: list[str] = []
    prompts = Prompts(
        say=lambda text: None,
        panel=lambda title, lines: None,
        ask=lambda label, default=None: (
            問過.append(label),
            "https://redmine.example.com/redmine" if "網址" in label else (default or "main"),
        )[1],
        ask_secret=lambda label: "k" * 40,
        confirm=lambda label: True,
        choose=lambda label, options: 0,
        choose_many=lambda label, options, default: list(default),
        open_url=lambda url: None,
    )
    code = run_setup(
        prompts,
        config_path=tmp_path / "config.toml",
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
    )
    assert code == 0
    assert any("代號" in label for label in 問過), "非 bundle 模式仍應問代號"


def test_setup_既有參數檔非UTF8時不拋例外(tmp_path):
    """既有參數檔不是 UTF-8 時，精靈要說明原因並收斂結束，而不是 traceback。"""
    target = tmp_path / "config.toml"
    # Windows 記事本以 cp950 存中文說明是常見情況。
    target.write_bytes('[sites.main]\ndescription = "正式站"\n'.encode("cp950"))
    script = 腳本化提問(answers=[], confirms=[True], choices=[])

    code = run_setup(
        script.build(),
        config_path=target,
        run=lambda argv, stdin=None: CommandResult(0, "", ""),
        verify=_成功驗證,
    )

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "UTF-8" in 全部輸出
    # 訊息不可帶出檔案內容（該檔可能放著 api_key）。
    assert "正式站" not in 全部輸出


def test_多選解析_空輸入採用預設():
    assert _parse_multi_select("", [0, 2], 3) == [0, 2]


def test_多選解析_逗號分隔的編號取代預設():
    assert _parse_multi_select("1,2", [0], 3) == [0, 1]


def test_多選解析_零代表都不要():
    assert _parse_multi_select("0", [0, 1, 2], 3) == []


def test_多選解析_帶空白的逗號分隔也吃():
    assert _parse_multi_select(" 1 , 3 ", [], 3) == [0, 2]


def test_多選解析_重複編號會去重():
    assert _parse_multi_select("1,1,2", [], 3) == [0, 1]


def test_多選解析_超出範圍回傳None():
    assert _parse_multi_select("4", [0], 3) is None


def test_多選解析_非數字回傳None():
    assert _parse_multi_select("abc", [0], 3) is None


def test_多選解析_混合合法與非法整體視為不合法():
    assert _parse_multi_select("1,x", [0], 3) is None


def _既有兩站設定檔(tmp_path):
    """建一份「已經裝好、有兩個站台」的參數檔，模擬重跑一鍵安裝的機器。"""
    設定檔 = tmp_path / "config.toml"
    設定檔.write_text(
        "[sites.main]\nurl = \"https://redmine.example.com/redmine\"\n"
        "api_key = \"main-key-1234\"\n\n"
        "[sites.new]\nurl = \"https://new.example.com/redmine\"\n"
        "api_key = \"new-key-1234\"\n",
        encoding="utf-8",
    )
    return 設定檔


def test_bundle_模式已有設定時先問要不要改而不是直接要網址(tmp_path):
    """真實案例：機器上已有 main、new 兩站，使用者重跑一鍵安裝只是想升級。

    精靈卻直接跳到「請貼上 Redmine 網址」，他按了三次 Enter，被判定連不上，
    整個安裝中止在參數檔這一步（[3/7]），後面的註冊與冒煙測試都沒跑到。
    已經設定過的機器要先問「這次要修改嗎？」，預設就是沿用、原樣不動。
    """
    設定檔 = _既有兩站設定檔(tmp_path)
    原內容 = 設定檔.read_text(encoding="utf-8")
    問過: list[str] = []

    prompts = Prompts(
        say=lambda text: None,
        panel=lambda title, lines: None,
        ask=lambda label, default=None: pytest.fail(f"沿用現有設定時不該再問：{label}"),
        ask_secret=lambda label: pytest.fail(f"沿用現有設定時不該再問：{label}"),
        confirm=lambda label: (問過.append(label), False)[1],
        choose=lambda label, options: pytest.fail(f"沿用現有設定時不該讓人選：{label}"),
        choose_many=lambda label, options, default: pytest.fail("不該多選"),
        open_url=lambda url: None,
    )
    code = run_setup(
        prompts,
        config_path=設定檔,
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    assert code == 0, "沿用現有設定要算成功，安裝才會繼續往註冊與冒煙測試走"
    assert 設定檔.read_text(encoding="utf-8") == 原內容, "答『不改』就不可以動到參數檔"
    assert len(問過) == 1


def test_bundle_模式多站要修改時讓人點要改哪一個(tmp_path):
    """答「要改」而站台不只一個時，無從猜要改哪一站，直接讓他點。

    選了之後網址要帶現值當預設，金鑰驗得過也能沿用——這正是既有單站已經有的
    待遇，不該因為多了一站就退化成從零重填。
    """
    設定檔 = _既有兩站設定檔(tmp_path)
    選項記錄: list[list[str]] = []
    預設記錄: list[str | None] = []

    prompts = Prompts(
        say=lambda text: None,
        panel=lambda title, lines: None,
        ask=lambda label, default=None: (
            預設記錄.append(default) if "網址" in label else None,
            default or "",
        )[1],
        ask_secret=lambda label: pytest.fail("金鑰驗得過且答『不換』時不該再問"),
        # 第一個 confirm 是「要修改嗎？」→ 要；第二個是「要換一把新的金鑰嗎？」→ 不換。
        confirm=lambda label: "要修改嗎" in label,
        choose=lambda label, options: (選項記錄.append(list(options)), 0)[1],
        choose_many=lambda label, options, default: pytest.fail("不該多選"),
        open_url=lambda url: None,
    )
    code = run_setup(
        prompts,
        config_path=設定檔,
        run=_固定回應(0),
        verify=_成功驗證,
        finish=False,
        bundle_mode=True,
    )
    assert code == 0
    assert 選項記錄, "多站時要讓使用者點要改哪一個"
    assert "main" in 選項記錄[0] and "new" in 選項記錄[0]
    assert 預設記錄 == ["https://redmine.example.com/redmine"], "網址要帶選中那站的現值"
    assert 'api_key     = "main-key-1234"' in 設定檔.read_text(encoding="utf-8")


# --- 方向鍵選單（單選／多選） -------------------------------------------------


class _假問句:
    """假的 questionary 提問物件：記下拿到的參數，`unsafe_ask()` 回固定答案。"""

    def __init__(self, 回傳, 紀錄: dict):
        self._回傳 = 回傳
        self._紀錄 = 紀錄

    def unsafe_ask(self):
        return self._回傳


def _假questionary(回傳, 紀錄: dict):
    """組一個只夠本模組使用的假 questionary 模組。"""
    import types

    class Choice:
        def __init__(self, title, value=None, checked=False):
            self.title = title
            self.value = value
            self.checked = checked

    def checkbox(message, choices, instruction=None):
        紀錄["message"] = message
        紀錄["choices"] = choices
        紀錄["instruction"] = instruction
        return _假問句(回傳, 紀錄)

    def select(message, choices, instruction=None):
        紀錄["message"] = message
        紀錄["choices"] = choices
        紀錄["instruction"] = instruction
        return _假問句(回傳, 紀錄)

    def password(message):
        紀錄["message"] = message
        return _假問句(回傳, 紀錄)

    模組 = types.ModuleType("questionary")
    模組.Choice = Choice
    模組.checkbox = checkbox
    模組.select = select
    模組.password = password
    return 模組


def test_方向鍵多選可以一次勾多個(monkeypatch):
    """空白鍵勾選要能同時勾多個，回傳的是全部勾中的索引（已排序）。"""
    import sys

    from redmine_mcp.setup import wizard

    紀錄: dict = {}
    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)
    monkeypatch.setitem(sys.modules, "questionary", _假questionary([2, 0], 紀錄))

    結果 = wizard._方向鍵多選(
        "要讓哪些讀得到？", ["claude-code", "gemini-cli", "opencode"], [0, 1], lambda 行: None
    )
    assert 結果 == [0, 2]
    assert [選項.checked for 選項 in 紀錄["choices"]] == [True, True, False], "預設勾選要帶進去"
    assert "空白鍵" in 紀錄["instruction"], "操作說明要講清楚怎麼勾"


def test_方向鍵多選勾零個也是合法答案(monkeypatch):
    """一個都不勾要回空清單，不能被當成「選單畫不出來」而退回輸入編號。"""
    import sys

    from redmine_mcp.setup import wizard

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)
    monkeypatch.setitem(sys.modules, "questionary", _假questionary([], {}))

    assert wizard._方向鍵多選("要哪些？", ["a", "b"], [], lambda 行: None) == []


def test_多行提問只把最後一行當問句(monkeypatch):
    """多行提問要先印前言、只留最後一行當問句，選單才不會擠成一團。"""
    import sys

    from redmine_mcp.setup import wizard

    紀錄: dict = {}
    印過: list[str] = []
    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)
    monkeypatch.setitem(sys.modules, "questionary", _假questionary(1, 紀錄))

    結果 = wizard._方向鍵單選(
        "偵測到既有設定：站台 main\n要怎麼處理？", ["取代", "新增"], 印過.append
    )
    assert 結果 == 1
    assert 印過 == ["偵測到既有設定：站台 main"]
    assert 紀錄["message"] == "要怎麼處理？"


def test_選單畫不出來時回_none_讓呼叫端退回輸入編號(monkeypatch):
    """字碼頁 950 的主控台畫不出 questionary 的字元、CI 沒有終端可用。

    這些情況都不能讓安裝中斷——選單只是輸入方式，畫不出來就退回原本輸入編號的問法。
    """
    import sys

    from redmine_mcp.setup import wizard

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)

    def 會爆的checkbox(*args, **kwargs):
        raise UnicodeEncodeError("cp950", "x", 0, 1, "illegal multibyte sequence")

    壞掉的 = _假questionary(None, {})
    壞掉的.checkbox = 會爆的checkbox
    壞掉的.select = 會爆的checkbox
    monkeypatch.setitem(sys.modules, "questionary", 壞掉的)

    assert wizard._方向鍵多選("要哪些？", ["a"], [], lambda 行: None) is None
    assert wizard._方向鍵單選("要哪個？", ["a"], lambda 行: None) is None


def test_不是終端時完全不碰_questionary(monkeypatch):
    """pytest／管線執行下 stdin 不是終端，必須直接退回輸入編號的問法。"""
    from redmine_mcp.setup import wizard

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: False)
    assert wizard._方向鍵多選("要哪些？", ["a"], [], lambda 行: None) is None
    assert wizard._方向鍵單選("要哪個？", ["a"], lambda 行: None) is None


def test_多選清單的勾選記號不會被_rich_標記吃掉(tmp_path, monkeypatch):
    """`[x]` 會被 Rich 當成標記解析掉，畫面與紀錄檔都只剩空白。

    真實案例（安裝紀錄實測）：已勾選的前兩列印成「   1. claude-code」，未勾選的
    卻有「  [ ] 3. opencode」——因為 `[ ]` 含空白不是合法標記所以留下來，`[x]`
    卻被吃掉。使用者因此完全看不出哪幾項是勾起來的。
    """
    import typer

    from redmine_mcp.setup.wizard import console_prompts

    # pytest 會攔截 stdin，typer.prompt 直接讀會拋 OSError；這裡只驗印出去與記錄
    # 下來的清單長相，答案本身不是重點，餵空字串（＝接受預設）即可。
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "")
    紀錄檔 = tmp_path / "安裝紀錄.txt"
    prompts = console_prompts(log_path=紀錄檔)
    # 沒有終端可用（pytest），會走輸入編號的退路。
    prompts.choose_many("要哪些？", ["claude-code", "opencode"], [0])
    內容 = 紀錄檔.read_text(encoding="utf-8")
    assert "[x] 1. claude-code" in 內容, "勾起來的那一列要看得出勾號"
    assert "[ ] 2. opencode" in 內容


def test_方向鍵選單畫不出來時要說一句而不是靜默退回(monkeypatch):
    """靜默退回等於把病因藏起來。

    真實案例：使用者的紀錄檔看不出畫面到底是方向鍵選單還是輸入編號的退路，
    因為兩條路徑印進紀錄的內容一模一樣。畫不出來時印一行原因，下次就查得到。
    """
    import sys

    from redmine_mcp.setup import wizard

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)

    def 會爆的(*args, **kwargs):
        raise RuntimeError("no console screen buffer")

    壞掉的 = _假questionary(None, {})
    壞掉的.checkbox = 會爆的
    壞掉的.select = 會爆的
    monkeypatch.setitem(sys.modules, "questionary", 壞掉的)

    印過: list[str] = []
    assert wizard._方向鍵多選("要哪些？", ["a"], [], 印過.append) is None
    assert any("no console screen buffer" in 行 for 行 in 印過), (
        "要把真正的原因印出來，否則沒人查得到為什麼沒有方向鍵選單"
    )


def test_多選改過勾選後紀錄檔要記下最後結果(tmp_path, monkeypatch):
    """真實案例：預設勾選的工具被使用者取消後，紀錄檔仍寫著它被勾著。

    紀錄是在「問之前」寫的、寫的是預設狀態，之後沒有任何一行覆蓋它，於是拿到
    紀錄檔的 IT 會看到與使用者畫面完全相反的結果。選單的答案是從我們自己印出的
    固定清單裡挑的，不是自由輸入、更不是金鑰，記下來沒有外洩風險，而它正是 IT
    最需要知道的一件事。
    """
    import typer

    from redmine_mcp.setup.wizard import console_prompts

    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "2")
    紀錄檔 = tmp_path / "安裝紀錄.txt"
    prompts = console_prompts(log_path=紀錄檔)
    結果 = prompts.choose_many("要哪些？", ["claude-code", "gemini-cli"], [0])

    assert 結果 == [1]
    內容 = 紀錄檔.read_text(encoding="utf-8")
    assert "最後勾選" in 內容
    最後一段 = 內容[內容.index("最後勾選"):]
    assert "gemini-cli" in 最後一段
    assert "claude-code" not in 最後一段, "取消掉的不可以還記在最後結果裡"


def test_多選一個都不勾也要記下來(tmp_path, monkeypatch):
    import typer

    from redmine_mcp.setup.wizard import console_prompts

    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "0")
    紀錄檔 = tmp_path / "安裝紀錄.txt"
    console_prompts(log_path=紀錄檔).choose_many("要哪些？", ["a", "b"], [0, 1])
    assert "一個都沒勾" in 紀錄檔.read_text(encoding="utf-8")


def test_方向鍵選單選完的結果同樣要進紀錄(tmp_path, monkeypatch):
    """兩條路徑都要記，否則紀錄長相會隨環境而異，IT 讀到的東西不一致。"""
    import sys

    from redmine_mcp.setup import wizard

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)
    monkeypatch.setitem(sys.modules, "questionary", _假questionary([1], {}))
    紀錄檔 = tmp_path / "安裝紀錄.txt"
    結果 = wizard.console_prompts(log_path=紀錄檔).choose_many(
        "要哪些？", ["claude-code", "gemini-cli"], [0]
    )
    assert 結果 == [1]
    內容 = 紀錄檔.read_text(encoding="utf-8")
    assert "最後勾選" in 內容 and "gemini-cli" in 內容[內容.index("最後勾選"):]


def test_單選的結果也要進紀錄(tmp_path, monkeypatch):
    import typer

    from redmine_mcp.setup.wizard import console_prompts

    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "2")
    紀錄檔 = tmp_path / "安裝紀錄.txt"
    prompts = console_prompts(log_path=紀錄檔)
    索引 = prompts.choose("要哪一個？", ["取代原本的", "另外新增一個"])
    assert 索引 == 1
    內容 = 紀錄檔.read_text(encoding="utf-8")
    assert "選了" in 內容
    assert "另外新增一個" in 內容[內容.index("選了"):]


def test_字碼頁編不出中文時_typer_的提問也不會炸(monkeypatch):
    """CI 的 Windows runner（cp1252）實測抓到的：typer.prompt 會炸 UnicodeEncodeError。

    `_tolerant_stdout()` 只包住 Rich 的 Console，typer／click 走的是它自己解析出來的
    輸出流，完全沒被保護到。於是在字碼頁不是 UTF-8 的主控台（英文版 Windows、CI
    runner）上，只要走到「請輸入編號」這類中文提問就整個安裝中止——而那正是畫不出
    方向鍵選單時的退路，等於退路本身會炸。
    """
    import io
    import sys

    from redmine_mcp.setup.wizard import console_prompts

    假stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdout", 假stdout)
    monkeypatch.setattr(sys, "stdin", io.StringIO("42\n"))

    prompts = console_prompts()
    答 = prompts.ask("Redmine 網址（從瀏覽器位址欄複製即可）")

    assert 答 == "42", "提問的中文編不出來，但答案仍要讀得到"


def test_編得出中文的終端不動它的錯誤處理(monkeypatch):
    """UTF-8 終端本來就印得出中文，不必降級成 errors=replace。"""
    import io
    import sys

    from redmine_mcp.setup.wizard import console_prompts

    假stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")
    monkeypatch.setattr(sys, "stdout", 假stdout)
    console_prompts()
    assert 假stdout.errors == "strict"


def test_金鑰輸入會逐字回顯星號而不是完全不回顯(monkeypatch):
    """金鑰是整個精靈最常被「貼上」的欄位。

    完全不回顯（getpass）時，非 IT 同事貼完看到一片空白，無從判斷到底貼進去沒有，
    實際反應是再貼一次或按 Enter 試探。改用逐字顯示 `*` 的遮罩輸入，畫面上看得到
    一串星號，既不外洩金鑰也看得出長度對不對。
    """
    import sys

    import typer

    from redmine_mcp.setup import wizard
    from redmine_mcp.setup.wizard import console_prompts

    紀錄: dict = {}
    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)
    monkeypatch.setitem(sys.modules, "questionary", _假questionary("  abcd1234  ", 紀錄))

    def 不該被呼叫(*args, **kwargs):
        raise AssertionError("有遮罩輸入可用時不該退回不回顯的問法")

    monkeypatch.setattr(typer, "prompt", 不該被呼叫)

    assert console_prompts().ask_secret("請貼上 API 金鑰") == "abcd1234", (
        "貼上常帶尾隨空白，遮罩輸入這條路徑也要 strip"
    )
    assert 紀錄["message"] == "請貼上 API 金鑰"


def test_遮罩輸入畫不出來時退回不回顯的問法並說明原因(monkeypatch):
    """字碼頁 950 的主控台畫不出 questionary 的字元。

    遮罩只是輸入方式，畫不出來絕不能讓安裝中斷，要退回原本 getpass 的問法；
    而且必須印出原因，否則事後看紀錄檔完全分不出走的是哪一條路徑。
    """
    import sys

    import typer

    from redmine_mcp.setup import wizard
    from redmine_mcp.setup.wizard import console_prompts

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)

    def 會爆的password(*args, **kwargs):
        raise UnicodeEncodeError("cp950", "x", 0, 1, "illegal multibyte sequence")

    壞掉的 = _假questionary(None, {})
    壞掉的.password = 會爆的password
    monkeypatch.setitem(sys.modules, "questionary", 壞掉的)

    問過: dict = {}

    def 假prompt(label, **kwargs):
        問過["label"] = label
        問過["hide_input"] = kwargs.get("hide_input")
        return " zzz "

    monkeypatch.setattr(typer, "prompt", 假prompt)

    印過: list[str] = []
    monkeypatch.setattr(
        "rich.console.Console.print", lambda self, text="", **kwargs: 印過.append(str(text))
    )

    assert console_prompts().ask_secret("請貼上 API 金鑰") == "zzz"
    assert 問過["hide_input"] is True, "退路仍然不可以把金鑰明碼印在畫面上"
    assert any("畫不出" in 一行 for 一行 in 印過), "要說明為什麼換了問法"


def test_不是終端時金鑰完全不碰_questionary(monkeypatch):
    """pytest／管線執行下 stdin 不是終端，畫不了遮罩，必須直接走 getpass 的問法。"""
    import sys
    import types

    import typer

    from redmine_mcp.setup import wizard
    from redmine_mcp.setup.wizard import console_prompts

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: False)

    炸掉的 = types.ModuleType("questionary")

    def 不該被呼叫(*args, **kwargs):
        raise AssertionError("沒有終端時不該碰 questionary")

    炸掉的.password = 不該被呼叫
    monkeypatch.setitem(sys.modules, "questionary", 炸掉的)
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "k")

    assert console_prompts().ask_secret("請貼上 API 金鑰") == "k"


def test_遮罩輸入的金鑰不會被寫進紀錄檔(tmp_path, monkeypatch):
    """紀錄檔會整份傳給 IT，只能記提問、不能沾到金鑰本身。"""
    import sys

    from redmine_mcp.setup import wizard
    from redmine_mcp.setup.wizard import console_prompts

    monkeypatch.setattr(wizard, "_可用方向鍵選單", lambda: True)
    monkeypatch.setitem(sys.modules, "questionary", _假questionary("s3cret-key", {}))

    紀錄檔 = tmp_path / "安裝紀錄.txt"
    console_prompts(log_path=紀錄檔).ask_secret("請貼上 API 金鑰")

    內容 = 紀錄檔.read_text(encoding="utf-8")
    assert "請貼上 API 金鑰" in 內容
    assert "s3cret-key" not in 內容
