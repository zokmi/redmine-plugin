"""網址正規化與站台代號推導的測試。

這三個函式是精靈瘦身的基礎：使用者只貼一個網址，代號與子路徑都由這裡推出來，
推錯的後果是寫出 server 讀不了的參數檔或連不到的站台，因此逐案例釘死。
"""
from __future__ import annotations

import pytest

from redmine_mcp.config import _SITE_NAME_PATTERN
from redmine_mcp.setup.site_hints import derive_site_name, normalize_url, subpath_candidate


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("redmine.example.com/redmine", "https://redmine.example.com/redmine"),
        ("  https://redmine.example.com/redmine/  ", "https://redmine.example.com/redmine"),
        ("http://redmine.example.com/redmine", "http://redmine.example.com/redmine"),
        ("HTTPS://Redmine.Example.com/redmine", "HTTPS://Redmine.Example.com/redmine"),
    ],
)
def test_網址正規化(raw: str, expected: str):
    assert normalize_url(raw) == expected


def test_沒有子路徑時給出_redmine_候選():
    assert subpath_candidate("https://example.com") == "https://example.com/redmine"


def test_已有子路徑時不再給候選():
    assert subpath_candidate("https://example.com/redmine") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://redmine.example.com/redmine", "example"),
        ("https://www.example.com.tw/redmine", "example"),
        ("https://redmine.example.com:8443/redmine", "example"),
        ("https://192.168.1.10/redmine", "192"),
        ("https://redmine.example.com", "example"),
    ],
)
def test_代號由網址推導(url: str, expected: str):
    assert derive_site_name(url, []) == expected


def test_代號衝突時加序號():
    assert derive_site_name("https://redmine.example.com/redmine", ["example"]) == "example-2"
    assert (
        derive_site_name("https://redmine.example.com/redmine", ["example", "example-2"])
        == "example-3"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://redmine./redmine",       # 去掉前綴後什麼都不剩
        "https://_._/redmine",            # 全是非法字元
        "not a url at all",               # 根本不是網址
    ],
)
def test_推不出代號時退回_site(url: str):
    assert derive_site_name(url, []) == "site"


def test_推出的代號一律合法():
    # 代號會作為下載子目錄名，不合法即構成路徑穿越；這條是安全邊界，不是格式偏好。
    for url in [
        "https://redmine.example.com/redmine",
        "https://192.168.1.10/redmine",
        "https://_._/redmine",
    ]:
        assert _SITE_NAME_PATTERN.match(derive_site_name(url, []))
