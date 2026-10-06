"""參數檔維護命令的流程測試：以假 Prompts／假 CommandRunner／假 verify 逐分支驗證。"""
import tomllib

import pytest

from redmine_mcp.setup import config_ops, toml_edit
from redmine_mcp.setup.external import CommandResult
from tests.test_setup_wizard import 腳本化提問


def _成功執行(argv, stdin=None):
    return CommandResult(0, "", "")


_一個站台 = (
    "# 我的參數檔\n"
    "\n"
    'download_dir = "D:/dl"\n'
    "\n"
    "[sites.main]\n"
    'url         = "https://a/redmine"\n'
    'api_key     = "0123456789abcdef"\n'
    'description = "公司正式站"\n'
)


def test_參數檔不存在時指向_setup(tmp_path):
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    result = config_ops.load_current(
        script.build(), tmp_path / "config.toml", need_sites=True
    )

    assert result is None
    assert "setup" in "\n".join(script.said)
    script.assert_exhausted()


def test_舊的扁平格式被拒絕並指向_setup(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('url = "https://a/b"\napi_key = "k"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.load_current(script.build(), target, need_sites=True) is None
    全部輸出 = "\n".join(script.said)
    assert "扁平" in 全部輸出
    assert "setup" in 全部輸出
    script.assert_exhausted()


def test_沒有站台且命令需要站台時擋下(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('download_dir = "D:/dl"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.load_current(script.build(), target, need_sites=True) is None
    script.assert_exhausted()


def test_沒有站台但命令不需要站台時放行(tmp_path):
    # set-global 只動全域鍵，沒有站台也該能執行。
    target = tmp_path / "config.toml"
    target.write_text('download_dir = "D:/dl"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    loaded = config_ops.load_current(script.build(), target, need_sites=False)
    assert loaded is not None
    assert loaded.sites == {}


def test_寫入成功會收緊權限並提醒重新連線(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    呼叫過的指令 = []

    def run(argv, stdin=None):
        呼叫過的指令.append(argv)
        return CommandResult(0, "", "")

    script = 腳本化提問(answers=[], confirms=[True], choices=[])
    code = config_ops.commit(
        script.build(), target, _一個站台, ["預覽"], run=run, assume_yes=False
    )

    assert code == 0
    全部輸出 = "\n".join(script.said)
    assert "/mcp" in 全部輸出
    # os.replace 換掉 inode，新檔權限不會沿用舊檔；漏了這步等於把金鑰檔權限放回預設。
    # 斷言輸出而非斷言指令：Windows 走 icacls（經 run），其他平台走 Path.chmod（不經 run），
    # 只有這行訊息在兩個平台都代表「權限收緊真的執行過且成功」。
    assert "已收緊檔案權限" in 全部輸出
    script.assert_exhausted()


def test_輸出失敗也不會讓權限收緊被跳過(tmp_path, monkeypatch):
    """實測踩過的坑：終端輸出本身會失敗，而它會把後面的權限收緊整個跳過。

    在字碼頁 950 的終端上，Rich 印 `[green]✓[/green]` 會拋 UnicodeEncodeError。原本的
    順序是「寫檔 → 印已寫入 → 收緊權限」，於是那個例外會讓含 API key 的參數檔停在
    os.replace 之後繼承來的寬鬆權限上（實測 ACL 變成 (I)(M,DC)）。這條測試用「一開口就
    拋例外」的 say 模擬那個終端，斷言收緊在例外之前就已經發生。
    """
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    收緊過 = []

    monkeypatch.setattr(
        "redmine_mcp.setup.config_ops.tighten_permissions",
        lambda path, run: 收緊過.append(path) and None,
    )

    def 一開口就爆(text):
        raise UnicodeEncodeError("cp950", "✓", 0, 1, "illegal multibyte sequence")

    script = 腳本化提問(answers=[], confirms=[], choices=[])
    prompts = script.build()
    壞終端 = type(prompts)(
        say=一開口就爆,
        panel=lambda title, lines: None,
        ask=prompts.ask,
        ask_secret=prompts.ask_secret,
        confirm=prompts.confirm,
        choose=prompts.choose,
        choose_many=prompts.choose_many,
        open_url=prompts.open_url,
    )

    with pytest.raises(UnicodeEncodeError):
        config_ops.commit(壞終端, target, _一個站台, ["預覽"], run=_成功執行, assume_yes=True)

    assert 收緊過 == [target]


def test_寫檔遇到_OSError_時原檔不動(tmp_path, monkeypatch):
    # spec 明列的失效模式：write_config() 可能因唯讀目錄、權限不足等原因拋 OSError，
    # commit() 必須把原因印清楚、回傳 1，且不能讓原檔被動到。
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")

    def 一定失敗(path, content):
        raise OSError("磁碟空間不足")

    monkeypatch.setattr(config_ops, "write_config", 一定失敗)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.commit(
        script.build(), target, _一個站台.replace("D:/dl", "D:/new"), ["預覽"],
        run=_成功執行, assume_yes=True,
    )

    assert code == 1
    assert target.read_text(encoding="utf-8") == _一個站台
    assert "寫入失敗" in "\n".join(script.said)
    script.assert_exhausted()


def test_收緊權限失敗仍視為成功(tmp_path, monkeypatch):
    # spec 明列的另一個失效模式：tighten_permissions() 失敗時只印警告，仍視為
    # commit() 成功（回傳 0）——檔案這時已經寫好且可用，不該因權限沒收緊就整個失敗。
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    monkeypatch.setattr(
        config_ops, "tighten_permissions", lambda path, run: "icacls 以 exit code 1 結束"
    )
    script = 腳本化提問(answers=[], confirms=[True], choices=[])

    code = config_ops.commit(
        script.build(), target, _一個站台, ["預覽"], run=_成功執行, assume_yes=False
    )

    assert code == 0
    全部輸出 = "\n".join(script.said)
    assert "權限收緊失敗" in 全部輸出
    script.assert_exhausted()


def test_編輯結果無法通過驗證時不落地(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.commit(
        script.build(), target, "[sites.main\n", ["預覽"], run=_成功執行, assume_yes=True
    )

    assert code == 1
    assert target.read_text(encoding="utf-8") == _一個站台
    assert "回報" in "\n".join(script.said)
    script.assert_exhausted()


def test_使用者拒絕確認時不寫檔(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[False], choices=[])

    code = config_ops.commit(
        script.build(), target, _一個站台.replace("D:/dl", "D:/new"), ["預覽"],
        run=_成功執行, assume_yes=False,
    )

    assert code == 1
    assert "D:/dl" in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_assume_yes_跳過確認(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.commit(
        script.build(), target, _一個站台.replace("D:/dl", "D:/new"), ["預覽"],
        run=_成功執行, assume_yes=True,
    )

    assert code == 0
    assert "D:/new" in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_show_列出站台並遮罩金鑰(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.show(script.build(), config_path=target)

    全部輸出 = "\n".join(script.said)
    assert code == 0
    assert "main" in 全部輸出
    assert "https://a/redmine" in 全部輸出
    assert "****cdef" in 全部輸出
    assert "0123456789abcdef" not in 全部輸出
    assert "D:/dl" in 全部輸出
    script.assert_exhausted()


def test_show_對未設定的全域鍵明確標示(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[sites.main]\nurl = "https://a/b"\napi_key = "k"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.show(script.build(), config_path=target) == 0
    assert "未設定" in "\n".join(script.said)


def test_show_對舊格式照實顯示而不拒絕(tmp_path):
    # show 是唯讀的，沒有改壞檔案的風險，直接說明它等同站台 default 更有用。
    target = tmp_path / "config.toml"
    target.write_text('url = "https://a/b"\napi_key = "k"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.show(script.build(), config_path=target) == 0
    assert "default" in "\n".join(script.said)


def test_show_檔案不存在時回傳一(tmp_path):
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    assert config_ops.show(script.build(), config_path=tmp_path / "nope.toml") == 1


def test_apply_changes_每次重新定位區間():
    # 前一次編輯會讓舊 span 失效（長度變了），因此每個 change 都要重新定位。
    text = "[sites.main]\n"
    result = config_ops.apply_changes(
        text,
        config_ops.global_locator(),
        [("download_dir", "D:/dl"), ("upload_dir", "D:/up"), ("download_dir", None)],
    )
    assert result == 'upload_dir = "D:/up"\n[sites.main]\n'


def test_load_current_對非表格的_sites_鍵給出乾淨錯誤而非當機(tmp_path):
    # sites 是字串（truthy、非表格、也沒有 legacy 鍵）：classify() 會誤判成 empty，
    # 若沿用「raw.get('sites') or {}」的簡化解析，.items() 會直接丟 AttributeError。
    target = tmp_path / "config.toml"
    target.write_text('sites = "oops"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    result = config_ops.load_current(script.build(), target, need_sites=False)

    assert result is None
    全部輸出 = "\n".join(script.said)
    assert "表格" in 全部輸出
    script.assert_exhausted()


def test_show_對陣列型態的_sites_鍵給出乾淨錯誤而非當機(tmp_path):
    # 換一種 truthy 非表格值（陣列）覆蓋另一個函式，避免兩處都只驗過字串這一種形狀。
    target = tmp_path / "config.toml"
    target.write_text('sites = ["a"]\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.show(script.build(), config_path=target)

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "表格" in 全部輸出
    script.assert_exhausted()


async def _成功驗證(url, api_key):
    return "kenny"


async def _失敗驗證(url, api_key):
    from redmine_mcp.errors import RedmineError

    raise RedmineError("金鑰不對")


async def _不該被呼叫的驗證(url, api_key):
    raise AssertionError("這個情境不該打 Redmine")


def _寫好一個站台(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    return target


def _set_site(script, target, **kwargs):
    參數 = {
        "site": "main",
        "url": None,
        "description": None,
        "clear_description": False,
        "ask_key": False,
        "run": _成功執行,
        "verify": _成功驗證,
        "assume_yes": True,
    }
    參數.update(kwargs)
    return config_ops.set_site(script.build(), config_path=target, **參數)


def test_換網址會先驗證再寫入(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, url="https://new/redmine")

    assert code == 0
    內容 = target.read_text(encoding="utf-8")
    assert 'url         = "https://new/redmine"' in 內容
    assert "kenny" in "\n".join(script.said)


def test_只改說明不打_Redmine(tmp_path):
    # 只改一行說明卻付一次網路往返沒有道理；用會拋錯的 verify 當哨兵鎖住這件事。
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, description="新的說明", verify=_不該被呼叫的驗證)

    assert code == 0
    assert 'description = "新的說明"' in target.read_text(encoding="utf-8")


def test_清空說明會移除該行(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, clear_description=True, verify=_不該被呼叫的驗證)

    assert code == 0
    assert "description" not in target.read_text(encoding="utf-8")


def test_換金鑰時以不回顯方式取得並驗證(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=["新金鑰9876"], confirms=[], choices=[])

    code = _set_site(script, target, ask_key=True)

    assert code == 0
    assert 'api_key     = "新金鑰9876"' in target.read_text(encoding="utf-8")
    全部輸出 = "\n".join(script.said)
    # 預覽只能出現遮罩後的樣子。
    assert "****9876" in 全部輸出
    assert "新金鑰9876" not in 全部輸出
    script.assert_exhausted()


def test_驗證失敗可重填後成功(tmp_path):
    target = _寫好一個站台(tmp_path)
    嘗試 = []

    async def verify(url, api_key):
        嘗試.append((url, api_key))
        if len(嘗試) == 1:
            from redmine_mcp.errors import RedmineError

            raise RedmineError("金鑰不對")
        return "kenny"

    script = 腳本化提問(
        answers=["壞金鑰1111", "https://a/redmine", "好金鑰2222"],
        confirms=[True],  # 要重新輸入嗎？
        choices=[],
    )

    code = _set_site(script, target, ask_key=True, verify=verify)

    assert code == 0
    assert len(嘗試) == 2
    assert 'api_key     = "好金鑰2222"' in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_重試時輸入的新網址要真的寫入(tmp_path):
    # code review 抓到的 Critical：只帶 ask_key=True（沒帶 --url），第一次驗證失敗、
    # 重試時把網址也改成一個「與原本不同」的正確網址、第二次驗證成功。這時畫面顯示
    # 「驗證通過」，寫進檔案的網址必須是重試時輸入的那個，不能仍是舊網址——否則會出現
    # 使用者看到驗證通過、實際寫入內容卻對不上驗證過內容的情況。
    target = _寫好一個站台(tmp_path)
    嘗試 = []

    async def verify(url, api_key):
        嘗試.append((url, api_key))
        if len(嘗試) == 1:
            from redmine_mcp.errors import RedmineError

            raise RedmineError("金鑰不對")
        return "kenny"

    script = 腳本化提問(
        answers=["壞金鑰1111", "https://correct/redmine", "好金鑰2222"],
        confirms=[True],  # 要重新輸入嗎？
        choices=[],
    )

    code = _set_site(script, target, ask_key=True, verify=verify)

    assert code == 0
    assert len(嘗試) == 2
    assert 嘗試[1] == ("https://correct/redmine", "好金鑰2222")
    內容 = target.read_text(encoding="utf-8")
    assert 'url         = "https://correct/redmine"' in 內容
    全部輸出 = "\n".join(script.said)
    assert "https://correct/redmine" in 全部輸出
    script.assert_exhausted()


def test_重試時輸入的新金鑰要真的寫入(tmp_path):
    # code review 第 1 項：只帶 --url（ask_key=False），檔案裡的舊金鑰已失效。第一次
    # 驗證失敗、重試時網址原樣保留、但輸入了新的正確金鑰、第二次驗證成功。畫面顯示
    # 「驗證通過」，此時 api_key 必須跟著寫入新金鑰——否則落地的會是「新網址 + 從未
    # 驗證過的舊金鑰」這個組合，卻讓使用者以為兩者都已驗證過。
    target = _寫好一個站台(tmp_path)
    嘗試 = []

    async def verify(url, api_key):
        嘗試.append((url, api_key))
        if len(嘗試) == 1:
            from redmine_mcp.errors import RedmineError

            raise RedmineError("金鑰不對")
        return "kenny"

    script = 腳本化提問(
        answers=["https://new/redmine", "好金鑰2222"],
        confirms=[True],  # 要重新輸入嗎？
        choices=[],
    )

    code = _set_site(
        script, target, url="https://new/redmine", ask_key=False, verify=verify
    )

    assert code == 0
    assert len(嘗試) == 2
    assert 嘗試[1] == ("https://new/redmine", "好金鑰2222")
    內容 = target.read_text(encoding="utf-8")
    assert 'api_key     = "好金鑰2222"' in 內容
    全部輸出 = "\n".join(script.said)
    assert "****2222" in 全部輸出
    assert "好金鑰2222" not in 全部輸出
    script.assert_exhausted()


def test_重試時金鑰留白會被當場攔下重問(tmp_path):
    # code review 第 2 項：重試分支輸入空金鑰時要當場說清楚並讓使用者重填，
    # 不能讓它混進 changes、最後由 commit() 的驗證閘擋下再印出「請回報」這種
    # 誤導使用者的訊息。
    target = _寫好一個站台(tmp_path)
    嘗試 = []

    async def verify(url, api_key):
        嘗試.append((url, api_key))
        if len(嘗試) == 1:
            from redmine_mcp.errors import RedmineError

            raise RedmineError("金鑰不對")
        return "kenny"

    script = 腳本化提問(
        answers=["https://new/redmine", "", "好金鑰2222"],
        confirms=[True],
        choices=[],
    )

    code = _set_site(
        script, target, url="https://new/redmine", ask_key=False, verify=verify
    )

    assert code == 0
    assert len(嘗試) == 2
    內容 = target.read_text(encoding="utf-8")
    assert 'api_key     = "好金鑰2222"' in 內容
    全部輸出 = "\n".join(script.said)
    assert "不能留白" in 全部輸出
    assert "回報" not in 全部輸出
    script.assert_exhausted()


def test_驗證失敗且放棄時不寫檔(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=["壞金鑰1111"], confirms=[False], choices=[])

    code = _set_site(script, target, ask_key=True, verify=_失敗驗證)

    assert code == 1
    assert target.read_text(encoding="utf-8") == _一個站台
    script.assert_exhausted()


def test_站台不存在時列出現有代號(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, site="nope", url="https://x/y")

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "main" in 全部輸出
    assert "setup" in 全部輸出


def test_站台說明不可同時設定與清空(tmp_path):
    # set-global 對同一鍵既設定又清空會明確報錯，set_site() 對 description 這組
    # 矛盾旗標原本靜默讓 clear 勝出；比照 set-global 改為報錯，行為要一致。
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(
        script, target, description="新說明", clear_description=True,
        verify=_不該被呼叫的驗證,
    )

    assert code == 1
    assert "同時" in "\n".join(script.said)
    assert target.read_text(encoding="utf-8") == _一個站台
    script.assert_exhausted()


def test_不吃掉站台層的覆寫鍵(tmp_path):
    # render_site_block 只吐 url／api_key／description 三行，整塊重建會讓這裡的
    # download_dir 消失。這條測試就是為了鎖住「逐鍵編輯」這個決定。
    target = tmp_path / "config.toml"
    target.write_text(
        "[sites.main]\n"
        'url         = "https://a/redmine"\n'
        'api_key     = "0123456789abcdef"\n'
        'download_dir = "D:/only-main"\n',
        encoding="utf-8",
    )
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, url="https://new/redmine")

    assert code == 0
    內容 = target.read_text(encoding="utf-8")
    assert 'download_dir = "D:/only-main"' in 內容
    assert 'url         = "https://new/redmine"' in 內容


def test_完全不帶旗標時逐項詢問並以_Enter_保留原值(tmp_path):
    target = _寫好一個站台(tmp_path)
    # 網址與說明都直接 Enter（空字串）、不換金鑰 → 什麼都沒變，也不必打 Redmine。
    script = 腳本化提問(answers=["", ""], confirms=[False], choices=[])

    code = config_ops.set_site(
        script.build(),
        config_path=target,
        site="main",
        url=None,
        description=None,
        clear_description=False,
        ask_key=False,
        run=_成功執行,
        verify=_不該被呼叫的驗證,
        assume_yes=True,
    )

    assert code == 0
    assert target.read_text(encoding="utf-8") == _一個站台
    script.assert_exhausted()


def test_互動模式輸入減號代表清空說明(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=["", "-"], confirms=[False], choices=[])

    code = config_ops.set_site(
        script.build(),
        config_path=target,
        site="main",
        url=None,
        description=None,
        clear_description=False,
        ask_key=False,
        run=_成功執行,
        verify=_不該被呼叫的驗證,
        assume_yes=True,
    )

    assert code == 0
    assert "description" not in target.read_text(encoding="utf-8")
    script.assert_exhausted()


_兩個站台 = (
    "[sites.main]\n"
    'url     = "https://a/redmine"\n'
    'api_key = "aaaa1111"\n'
    "\n"
    "[sites.other]\n"
    'url     = "https://b/redmine"\n'
    'api_key = "bbbb2222"\n'
)


def test_刪除站台後只剩另一個(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_兩個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[True], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="other", run=_成功執行, assume_yes=False
    )

    assert code == 0
    內容 = target.read_text(encoding="utf-8")
    assert "[sites.main]" in 內容
    assert "sites.other" not in 內容
    script.assert_exhausted()


def test_刪除唯一站台會先警告啟動將失敗(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[True], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="main", run=_成功執行, assume_yes=False
    )

    assert code == 0
    全部輸出 = "\n".join(script.said)
    assert "啟動" in 全部輸出
    # 全域鍵與註解要留著，使用者可能只是想換一個站台重設。
    assert 'download_dir = "D:/dl"' in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_assume_yes_仍然印出警告(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="main", run=_成功執行, assume_yes=True
    )

    assert code == 0
    assert "啟動" in "\n".join(script.said)
    script.assert_exhausted()


def test_拒絕確認時不刪(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_兩個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[False], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="other", run=_成功執行, assume_yes=False
    )

    assert code == 1
    assert target.read_text(encoding="utf-8") == _兩個站台
    script.assert_exhausted()


def test_刪除不存在的站台會列出現有代號(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_兩個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="nope", run=_成功執行, assume_yes=True
    )

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "main" in 全部輸出 and "other" in 全部輸出


def _set_global(script, target, **kwargs):
    參數 = {
        "download_dir": None,
        "upload_dir": None,
        "max_attachment_mb": None,
        "clear": [],
        "run": _成功執行,
        "assume_yes": True,
    }
    參數.update(kwargs)
    return config_ops.set_global(script.build(), config_path=target, **參數)


def test_設定下載目錄(tmp_path):
    target = _寫好一個站台(tmp_path)
    存在的目錄 = tmp_path / "dl"
    存在的目錄.mkdir()
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, download_dir=str(存在的目錄))

    assert code == 0
    # 不比字面子字串（同時容許逸出與未逸出兩種形式的斷言幾乎恆真），改驗真正的
    # 契約：server 用 tomllib 讀回來的值必須等於使用者輸入的值，逸出／解逸出是否
    # 對稱由這個往返自然驗證，且跨平台一致。
    寫入值 = tomllib.loads(target.read_text(encoding="utf-8"))["download_dir"]
    assert 寫入值 == str(存在的目錄)


def test_目錄不存在時警告但仍寫入(tmp_path):
    # server 端不檢查目錄存在性，這裡拒絕寫入會與實際行為不一致。
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, download_dir=str(tmp_path / "還沒建"))

    assert code == 0
    assert "不存在" in "\n".join(script.said)
    assert "還沒建" in target.read_text(encoding="utf-8")


def test_附件上限寫成裸整數(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, max_attachment_mb=25)

    assert code == 0
    assert "max_attachment_mb = 25" in target.read_text(encoding="utf-8")


def test_附件上限必須大於零(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, max_attachment_mb=0)

    assert code == 1
    assert "大於 0" in "\n".join(script.said)
    assert "max_attachment_mb" not in target.read_text(encoding="utf-8")


def test_清空全域鍵(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, clear=["download_dir"])

    assert code == 0
    assert "download_dir" not in target.read_text(encoding="utf-8")


def test_清空未知鍵時列出可用鍵(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, clear=["api_key"])

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "download_dir" in 全部輸出
    assert "upload_dir" in 全部輸出


def test_同一鍵不可同時設定與清空(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, download_dir="D:/dl", clear=["download_dir"])

    assert code == 1
    assert "同時" in "\n".join(script.said)


def test_沒有站台也能改全域設定(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("# 只有註解\n", encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    # 沒有站台的檔案不該在「讀檔前置」就被站台問題擋住（set-global 與站台無關），
    # 但整份檔案確實不可用，因此會在寫檔前的驗證閘被攔下。
    code = _set_global(script, target, max_attachment_mb=5)

    assert code == 1
    assert "無法通過驗證" in "\n".join(script.said)
    assert target.read_text(encoding="utf-8") == "# 只有註解\n"


def test_跨命令連續編輯後版面與鍵值仍然正確(tmp_path):
    # 現有測試每條都是「乾淨手寫 fixture → 一個命令 → 斷言」，沒有一條測過
    # `redmine-mcp setup` 實際會寫出來的形狀（render_new_config／render_site_block
    # 的真實輸出），也沒有測過多個命令累積編輯後版面是否還正確——set-global 在區間
    # 尾端插鍵、set-site 就地換值時要保留對齊、remove-site 要收斂尾端換行，三者交互
    # 之後容易互相踩到。這裡用真實輸出當起點，依序跑五個編輯，每一步都用
    # tomllib.loads() 驗證鍵值，最後確認註解與另一個站台仍然存在。
    target = tmp_path / "config.toml"
    block_main = toml_edit.render_site_block(
        "main", "https://a/redmine", "aaaa1111aaaa", "公司正式站"
    )
    text = toml_edit.render_new_config(block_main, "D:/dl", None)
    block_other = toml_edit.render_site_block("other", "https://b/redmine", "bbbb2222bbbb", None)
    text = toml_edit.upsert_site_block(text, "other", block_other)
    target.write_text(text, encoding="utf-8")

    無提問 = 腳本化提問(answers=[], confirms=[], choices=[])

    # 步驟一：set-global 在區間尾端插鍵（原本只有 download_dir 一個全域鍵）。
    code = _set_global(無提問, target, upload_dir="D:/up")
    assert code == 0
    設定 = tomllib.loads(target.read_text(encoding="utf-8"))
    assert 設定["download_dir"] == "D:/dl"
    assert 設定["upload_dir"] == "D:/up"

    # 步驟二：set-site 就地換 main 的網址，要保留原本的對齊空白，且不能動到 other。
    code = _set_site(無提問, target, site="main", url="https://new-main/redmine")
    assert code == 0
    設定 = tomllib.loads(target.read_text(encoding="utf-8"))
    assert 設定["sites"]["main"]["url"] == "https://new-main/redmine"
    assert 設定["sites"]["main"]["description"] == "公司正式站"
    assert 設定["sites"]["other"]["url"] == "https://b/redmine"
    內容 = target.read_text(encoding="utf-8")
    assert 'url         = "https://new-main/redmine"' in 內容  # 對齊空白仍在

    # 步驟三：set-global 清空 download_dir（刪除區間中段的一個鍵）。
    code = _set_global(無提問, target, clear=["download_dir"])
    assert code == 0
    設定 = tomllib.loads(target.read_text(encoding="utf-8"))
    assert "download_dir" not in 設定
    assert 設定["upload_dir"] == "D:/up"

    # 步驟四：再對 main 換一次說明，確認前面的編輯沒有讓區塊定位跑掉。
    code = _set_site(無提問, target, site="main", description="換過的說明")
    assert code == 0
    設定 = tomllib.loads(target.read_text(encoding="utf-8"))
    assert 設定["sites"]["main"]["description"] == "換過的說明"

    # 步驟五：remove-site 收斂尾端換行；剩下的 other 站台與最上方的說明註解要還在。
    script = 腳本化提問(answers=[], confirms=[True], choices=[])
    code = config_ops.remove_site(
        script.build(), config_path=target, site="main", run=_成功執行, assume_yes=False
    )
    assert code == 0
    最終內容 = target.read_text(encoding="utf-8")
    設定 = tomllib.loads(最終內容)
    assert "main" not in 設定.get("sites", {})
    assert 設定["sites"]["other"]["url"] == "https://b/redmine"
    assert "redmine-mcp 參數檔" in 最終內容  # render_new_config() 產生的說明註解仍在
    assert not 最終內容.endswith("\n\n")  # 尾端換行有收斂，沒有累積空白


def test_不帶任何旗標時逐項詢問(tmp_path):
    target = _寫好一個站台(tmp_path)
    # 下載目錄 Enter 保留、上傳目錄輸入新值、附件上限輸入 -（清空，本來就沒設）
    script = 腳本化提問(answers=["", str(tmp_path), "-"], confirms=[], choices=[])

    code = _set_global(script, target)

    assert code == 0
    設定 = tomllib.loads(target.read_text(encoding="utf-8"))
    assert 設定["download_dir"] == "D:/dl"
    # 同理改驗真正的契約：upload_dir 讀回來的值必須等於使用者輸入的值。
    assert 設定["upload_dir"] == str(tmp_path)
    script.assert_exhausted()


def test_讀不到參數檔時給出修復指令而不是_traceback(tmp_path, monkeypatch):
    """0.4 系列的 icacls 指令用裸帳號名，在某些機器上會把檔案授權給非本人的主體，
    結果連自己都讀不到。那種檔案落到這裡時，使用者需要的是一句「怎麼修」，
    而不是一坨 PermissionError 的 traceback。
    """
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")

    def 讀不到(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", 讀不到)

    script = 腳本化提問(answers=[], confirms=[], choices=[])
    assert config_ops.load_current(script.build(), target, need_sites=True) is None
    全部輸出 = "\n".join(script.said)
    assert "讀不到參數檔" in 全部輸出
    # 修復指令要指名這個檔案，使用者才能直接複製貼上。
    assert str(target) in 全部輸出

    script2 = 腳本化提問(answers=[], confirms=[], choices=[])
    assert config_ops.show(script2.build(), config_path=target) == 1
    assert "讀不到參數檔" in "\n".join(script2.said)
