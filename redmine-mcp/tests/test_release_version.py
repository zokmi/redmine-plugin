"""發佈版本一致性檢查的測試。

這段邏輯只在打 tag 時跑一次，出錯的代價卻是發出一個版本標示錯誤的 release，
事後只能刪 tag 重來。因此分支要在這裡驗完，而不是靠實際打 tag 試。
"""
from __future__ import annotations

import pytest
from check_release_version import check, main, parse_tag_version, read_pyproject_version

PYPROJECT = """
[project]
name = "redmine-mcp"
version = "0.1.0"
"""


def test_正常的_tag_取得版本():
    assert parse_tag_version("redmine-mcp-v0.1.0") == "0.1.0"


def test_接受_refs_tags_開頭的完整_ref():
    # workflow 可能傳 github.ref（refs/tags/...）而非 github.ref_name，
    # 兩種都收，免得換個寫法就整條檢查失效。
    assert parse_tag_version("refs/tags/redmine-mcp-v2.10.3") == "2.10.3"


@pytest.mark.parametrize(
    "tag",
    [
        "v0.1.0",  # 少了套件前綴：monorepo 中會與其他工具的 tag 混淆
        "redmine-mcp-0.1.0",  # 少了 v
        "other-mcp-v0.1.0",  # 別的套件
        "redmine-mcp-v",  # 沒有版本段
    ],
)
def test_前綴不符的_tag_被拒絕(tag: str):
    with pytest.raises(ValueError) as exc:
        parse_tag_version(tag)

    # 訊息要給出正確格式，否則發版的人得回去翻 workflow 才知道該怎麼打。
    assert "redmine-mcp-v" in str(exc.value)


@pytest.mark.parametrize(
    "tag",
    [
        "redmine-mcp-v0.1",  # 只有兩段
        "redmine-mcp-v0.1.0.1",  # 四段
        "redmine-mcp-v0.1.0-rc1",  # 目前不支援 prerelease
        "redmine-mcp-vx.y.z",  # 非數字
        "redmine-mcp-v０.１.０",  # 全形數字：正規式用 \\d 會誤放行
    ],
)
def test_版本段格式錯誤被拒絕(tag: str):
    with pytest.raises(ValueError) as exc:
        parse_tag_version(tag)

    assert "X.Y.Z" in str(exc.value)


def test_讀得到_pyproject_的版本():
    assert read_pyproject_version(PYPROJECT) == "0.1.0"


def test_pyproject_缺少版本時給出可行動的錯誤():
    with pytest.raises(ValueError) as exc:
        read_pyproject_version('[project]\nname = "redmine-mcp"\n')

    assert "project.version" in str(exc.value)


def test_版本相符時回傳該版本():
    assert check("redmine-mcp-v0.1.0", PYPROJECT) == "0.1.0"


def test_版本不符時兩邊的值都要出現在訊息裡():
    # 只說「不一致」的話，發版的人不知道該改 tag 還是改 pyproject。
    with pytest.raises(ValueError) as exc:
        check("redmine-mcp-v0.2.0", PYPROJECT)

    message = str(exc.value)
    assert "0.2.0" in message
    assert "0.1.0" in message


def test_main_相符時回傳_0(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")

    assert main(["redmine-mcp-v0.1.0", str(pyproject)]) == 0


def test_main_不符時回傳非零並輸出原因(tmp_path, capsys):
    # workflow 靠 exit code 中止，靠 stderr 讓人看懂為什麼中止；兩者缺一不可。
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")

    assert main(["redmine-mcp-v9.9.9", str(pyproject)]) != 0
    assert "9.9.9" in capsys.readouterr().err
