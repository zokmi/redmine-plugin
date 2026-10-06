import pytest

from redmine_mcp.config import ConfigError
from redmine_mcp.setup.toml_edit import (
    classify,
    convert_legacy,
    escape_toml,
    find_site_block,
    global_region,
    mask_secret,
    remove_key_in,
    remove_site_block,
    render_new_config,
    render_site_block,
    set_key_in,
    upsert_site_block,
    validate_config_text,
)


def test_逸出雙引號與反斜線():
    assert escape_toml(r'a"b\c') == r'a\"b\\c'


def test_逸出保持一般字元不變():
    assert escape_toml("https://redmine.example.com/redmine") == (
        "https://redmine.example.com/redmine"
    )


def test_遮罩只留末四碼():
    assert mask_secret("0123456789abcdef") == "****cdef"


def test_過短的金鑰整串遮掉():
    # 末四碼規則遇到短字串會等於全部露出，那比不遮更危險。
    assert mask_secret("abc") == "****"


def test_空字串判定為_empty():
    assert classify("") == "empty"


def test_只有全域鍵仍判定為_empty():
    # 有 download_dir 但沒有任何站台，仍屬「還沒設定過站台」。
    assert classify('download_dir = "D:/dl"\n') == "empty"


def test_有_sites_區塊判定為_sites():
    assert classify('[sites.main]\nurl = "https://a/b"\napi_key = "k"\n') == "sites"


def test_最外層有_api_key_判定為_legacy():
    assert classify('url = "https://a/b"\napi_key = "k"\n') == "legacy"


def test_語法錯誤直接拋出而不是猜測型態():
    with pytest.raises(ConfigError):
        classify("[sites.main\n")


def test_產生站台區塊含選填說明():
    block = render_site_block("main", "https://a/b", "key1", "公司正式站")
    assert block == (
        "[sites.main]\n"
        'url         = "https://a/b"\n'
        'api_key     = "key1"\n'
        'description = "公司正式站"\n'
    )


def test_產生站台區塊省略未填的說明():
    block = render_site_block("main", "https://a/b", "key1", None)
    assert "description" not in block


def test_找不到區塊回傳_None():
    assert find_site_block('[sites.main]\nurl = "x"\n', "other") is None


def test_定位到檔中的區塊只涵蓋自己那段():
    # 區塊尾端與下一個標頭之間的空行現在歸屬於「下一個區塊」，不再算進本區塊——
    # 這樣寫在下一站標頭上方的說明註解才不會被本區塊的邊界一併吃掉。
    text = '[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'
    start, end = find_site_block(text, "a")
    assert text[start:end] == '[sites.a]\nurl = "1"\n'


def test_定位到檔尾的區塊涵蓋到結尾():
    text = '[sites.a]\nurl = "1"\n[sites.b]\nurl = "2"\n'
    start, end = find_site_block(text, "b")
    assert text[start:end] == '[sites.b]\nurl = "2"\n'


def test_新站台附加在檔尾且保留原有註解():
    text = '# 我的註解\n[sites.a]\nurl = "1"\n'
    result = upsert_site_block(text, "b", '[sites.b]\nurl = "2"\n')
    assert result == '# 我的註解\n[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'


def test_附加時補上缺少的結尾換行():
    text = '[sites.a]\nurl = "1"'
    result = upsert_site_block(text, "b", '[sites.b]\nurl = "2"\n')
    assert result == '[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'


def test_取代既有區塊不動其他站台():
    text = '[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'
    result = upsert_site_block(text, "a", '[sites.a]\nurl = "NEW"\n')
    assert result == '[sites.a]\nurl = "NEW"\n\n[sites.b]\nurl = "2"\n'


def test_解析得到站台卻定位不到就拋出而非靜默附加():
    # 引號寫法 ["sites"."main"] tomllib 讀得到，但行首樣式比對不到。
    # 這時若靜默附加，檔案會出現重複站台而 server 啟動即失敗——寧可當場說清楚。
    text = '["sites"."main"]\nurl = "1"\n'
    with pytest.raises(ConfigError):
        upsert_site_block(text, "main", '[sites.main]\nurl = "2"\n')


def test_新檔含說明註解與站台區塊():
    text = render_new_config('[sites.main]\nurl = "https://a/b"\n', None, None)
    assert text.startswith("#")
    assert '[sites.main]' in text
    assert "download_dir" not in text


def test_新檔帶下載與上傳目錄():
    text = render_new_config("[sites.main]\n", "D:/dl", "D:/up")
    assert 'download_dir = "D:/dl"' in text
    assert 'upload_dir   = "D:/up"' in text


def test_舊格式轉成_sites_default_並保留全域鍵():
    text = 'download_dir = "D:/dl"\nurl = "https://a/b"\napi_key = "k"\n'
    result = convert_legacy(text)
    assert classify(result) == "sites"
    assert 'download_dir = "D:/dl"' in result
    assert "[sites.default]" in result
    assert 'api_key     = "k"' in result


def test_轉換保留原有的說明():
    text = 'url = "https://a/b"\napi_key = "k"\ndescription = "舊站"\n'
    assert 'description = "舊站"' in convert_legacy(text)


def test_不是舊格式就拒絕轉換():
    with pytest.raises(ConfigError):
        convert_legacy('[sites.main]\nurl = "x"\n')


_合法內容 = '[sites.main]\nurl = "https://a/b"\napi_key = "k"\n'


def test_合法內容通過驗證():
    validate_config_text(_合法內容)  # 不拋出即通過


def test_壞掉的_TOML_被擋下且訊息不帶原文():
    # 出錯那一行有可能正好是 api_key，訊息只能講「無法解析」。
    壞內容 = '[sites.main]\napi_key = "還沒收尾的字串\n'
    with pytest.raises(ConfigError) as exc:
        validate_config_text(壞內容)
    assert "api_key" not in str(exc.value)


def test_缺少必填鍵被擋下():
    with pytest.raises(ConfigError):
        validate_config_text('[sites.main]\nurl = "https://a/b"\n')


def test_沒有站台時預設不通過():
    # load_settings() 會補一個 default 站台再抱怨缺 url；這對一般編輯正是我們要的攔阻。
    with pytest.raises(ConfigError):
        validate_config_text('download_dir = "D:/dl"\n')


def test_允許沒有站台時放行():
    # 刪掉最後一個站台是 spec 明文允許的操作（會另外警告），不能被這道閘擋死。
    validate_config_text('download_dir = "D:/dl"\n', allow_no_sites=True)


def test_允許沒有站台不會放行其他錯誤():
    with pytest.raises(ConfigError):
        validate_config_text('[sites.main]\nurl = "https://a/b"\n', allow_no_sites=True)


def test_全域區到第一個表格標頭為止():
    text = '# 註解\ndownload_dir = "D:/dl"\n\n[sites.main]\nurl = "https://a/b"\n'
    start, end = global_region(text)
    assert text[start:end] == '# 註解\ndownload_dir = "D:/dl"\n\n'


def test_沒有任何表格標頭時全域區是整份內容():
    text = 'download_dir = "D:/dl"\n'
    assert global_region(text) == (0, len(text))


def test_設定既有的全域鍵是就地換行():
    text = 'download_dir = "D:/old"\n\n[sites.main]\nurl = "https://a/b"\n'
    result = set_key_in(text, global_region(text), "download_dir", "D:/new")
    assert result == 'download_dir = "D:/new"\n\n[sites.main]\nurl = "https://a/b"\n'


def test_就地換值時保留原本的欄位對齊空白():
    # render_site_block() 會把 url／api_key／description 對齊到同一欄（例如這裡的
    # `url         = `）。這條測試直接鎖 set_key_in() 本身：換值時只能動 `=` 後面的
    # 值，不能把使用者（或 render_site_block）打好的對齊空白一併吃掉、整行重寫成
    # `url = "..."`。先前這個行為只靠 config_ops 測試裡的字串斷言間接驗證，鎖點放錯
    # 層級，這裡把它鎖回 toml_edit 這一層。
    text = (
        "[sites.main]\n"
        'url         = "https://old"\n'
        'api_key     = "0123456789abcdef"\n'
    )
    result = set_key_in(text, (0, len(text)), "url", "https://new")
    assert 'url         = "https://new"' in result
    # 沒被動到的那一行也要原封不動，確認只換了目標鍵。
    assert 'api_key     = "0123456789abcdef"' in result


def test_全域鍵不存在時插在註解之後並隔一空行():
    text = "# redmine-mcp 參數檔\n\n[sites.main]\nurl = \"https://a/b\"\n"
    result = set_key_in(text, global_region(text), "download_dir", "D:/dl")
    assert result == (
        "# redmine-mcp 參數檔\n"
        "\n"
        'download_dir = "D:/dl"\n'
        "\n"
        "[sites.main]\n"
        'url = "https://a/b"\n'
    )


def test_已有全域鍵時新鍵緊接在後不多插空行():
    text = 'download_dir = "D:/dl"\n\n[sites.main]\nurl = "https://a/b"\n'
    result = set_key_in(text, global_region(text), "upload_dir", "D:/up")
    assert result == (
        'download_dir = "D:/dl"\n'
        'upload_dir = "D:/up"\n'
        "\n"
        "[sites.main]\n"
        'url = "https://a/b"\n'
    )


def test_整數值不加引號():
    # config_file 的 _scalar() 會 str() 兩種寫法都讀得到，裸整數與 config.example.toml 一致。
    text = "[sites.main]\n"
    result = set_key_in(text, (0, 0), "max_attachment_mb", 25)
    assert result == "max_attachment_mb = 25\n[sites.main]\n"


def test_字串值逸出引號與反斜線():
    result = set_key_in("", (0, 0), "download_dir", r'D:\a"b')
    assert result == 'download_dir = "D:\\\\a\\"b"\n'


def test_註解掉的同名行不算既有鍵():
    # 把註解行當成該鍵去替換，會讓使用者的說明消失、真正的設定又沒寫進去。
    text = '# download_dir = "D:/old"\n\n[sites.main]\n'
    result = set_key_in(text, global_region(text), "download_dir", "D:/new")
    assert '# download_dir = "D:/old"' in result
    assert 'download_dir = "D:/new"' in result


def test_只在指定區間內尋找同名鍵():
    # [sites.main] 底下也可以有 download_dir；改全域鍵絕不能動到站台層的覆寫。
    text = (
        'download_dir = "D:/global"\n'
        "\n"
        "[sites.main]\n"
        'download_dir = "D:/site"\n'
    )
    result = set_key_in(text, global_region(text), "download_dir", "D:/new")
    assert 'download_dir = "D:/new"' in result
    assert 'download_dir = "D:/site"' in result


def test_刪除區間內的鍵連整行一起移除():
    text = 'download_dir = "D:/dl"\nupload_dir = "D:/up"\n\n[sites.main]\n'
    result = remove_key_in(text, global_region(text), "download_dir")
    assert result == 'upload_dir = "D:/up"\n\n[sites.main]\n'


def test_刪除不存在的鍵是冪等的():
    # --clear 可能被重複執行，第二次不該報錯。
    text = 'upload_dir = "D:/up"\n\n[sites.main]\n'
    assert remove_key_in(text, global_region(text), "download_dir") == text


def test_刪除中間的站台區塊不影響其他站台():
    text = (
        "[sites.main]\n"
        'url = "https://a/b"\n'
        "\n"
        "[sites.mid]\n"
        'url = "https://m/m"\n'
        "\n"
        "[sites.last]\n"
        'url = "https://l/l"\n'
    )
    result = remove_site_block(text, "mid")
    assert result == (
        "[sites.main]\n"
        'url = "https://a/b"\n'
        "\n"
        "[sites.last]\n"
        'url = "https://l/l"\n'
    )


def test_刪除最後一個區塊不留下多餘空行():
    text = '[sites.main]\nurl = "https://a/b"\n\n[sites.tail]\nurl = "https://t/t"\n'
    result = remove_site_block(text, "tail")
    assert result == '[sites.main]\nurl = "https://a/b"\n'


def test_刪除唯一站台後保留全域鍵與註解():
    text = '# 我的參數檔\n\ndownload_dir = "D:/dl"\n\n[sites.main]\nurl = "https://a/b"\n'
    result = remove_site_block(text, "main")
    assert result == '# 我的參數檔\n\ndownload_dir = "D:/dl"\n'


def test_刪光之後沒有殘留內容時回傳空字串():
    assert remove_site_block('[sites.main]\nurl = "https://a/b"\n', "main") == ""


def test_刪除不存在的站台會拋出():
    with pytest.raises(ConfigError):
        remove_site_block('[sites.main]\nurl = "https://a/b"\n', "nope")


# 下一個站台標頭上方的註解屬於「下一站」，不是前一站區塊的尾巴。
_TEXT_WITH_NEXT_SITE_COMMENT = """[sites.main]
url         = "https://a/redmine"   # 正式站
api_key     = "AAA"

# 下面這站是測試機，記得別亂寫
[sites.new]
url         = "https://b/redmine"
api_key     = "BBB"
"""


def test_remove_site_保留下一站的說明註解():
    result = remove_site_block(_TEXT_WITH_NEXT_SITE_COMMENT, "main")

    assert "# 下面這站是測試機，記得別亂寫" in result
    assert "[sites.main]" not in result
    assert "[sites.new]" in result


def test_upsert_取代站台時保留下一站註解且不塌版面():
    block = render_site_block("main", url="https://zzz/redmine", api_key="CCC", description=None)
    result = upsert_site_block(_TEXT_WITH_NEXT_SITE_COMMENT, "main", block)

    assert "# 下面這站是測試機，記得別亂寫" in result
    # 註解與新區塊之間要留白，不可緊貼。
    assert "\n\n# 下面這站是測試機" in result
    assert "https://zzz/redmine" in result
    assert "https://a/redmine" not in result


def test_set_key_in_保留行內註解():
    span = find_site_block(_TEXT_WITH_NEXT_SITE_COMMENT, "main")
    assert span is not None
    result = set_key_in(_TEXT_WITH_NEXT_SITE_COMMENT, span, "url", "https://zzz/redmine")

    # 斷言完整字面，而不只是各自 in result：只斷言兩個片段各自存在，即使值與
    # 註解間的空白被歸零（黏成 `"https://zzz/redmine"# 正式站`）也會通過，鎖不住
    # 版面對齊被破壞的迴歸。
    assert 'url         = "https://zzz/redmine"   # 正式站' in result


def test_set_key_in_值內含井號不被當成註解():
    text = '[sites.main]\napi_key = "old"\n'
    span = find_site_block(text, "main")
    assert span is not None
    # 井號在引號內是值的一部分，不是註解起點。
    result = set_key_in(text, span, "api_key", "ab#cd")

    assert 'api_key = "ab#cd"' in result

    # 反過來：原本帶行內註解時，換值後註解要留著、值要完整替換。
    text2 = '[sites.main]\napi_key = "a#b"   # 舊金鑰\n'
    span2 = find_site_block(text2, "main")
    assert span2 is not None
    result2 = set_key_in(text2, span2, "api_key", "new")

    assert "# 舊金鑰" in result2
    assert 'api_key = "new"' in result2
    assert "a#b" not in result2
