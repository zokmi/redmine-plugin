import subprocess
import sys

import pytest

from redmine_mcp.setup.fsops import write_config


def test_寫入新檔並建立父目錄(tmp_path):
    target = tmp_path / "nested" / "config.toml"
    write_config(target, "url = 1\n")
    assert target.read_text(encoding="utf-8") == "url = 1\n"


def test_覆寫既有檔不留暫存檔(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("old\n", encoding="utf-8")
    write_config(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert list(tmp_path.iterdir()) == [target]


def test_寫入失敗時原檔不變且不留暫存檔(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    target.write_text("old\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        write_config(target, "new\n")
    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.iterdir()) == [target]


def test_寫入時遇到_KeyboardInterrupt_原檔不變且不留暫存檔(tmp_path, monkeypatch):
    # KeyboardInterrupt 繼承 BaseException、不是 OSError，若 write_config 只攔
    # OSError，Ctrl-C 會跳過暫存檔清理直接往外拋，讓暫存檔留在目標目錄下。
    target = tmp_path / "config.toml"
    target.write_text("old\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(KeyboardInterrupt):
        write_config(target, "new\n")
    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode")
def test_config_mode_is_private(tmp_path):
    target = tmp_path / "config.toml"
    write_config(target, "private")
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL")
def test_config_acl_excludes_inherited_access_before_replace(tmp_path, monkeypatch):
    import os

    target = tmp_path / "config.toml"
    subprocess.run(
        ["icacls", str(tmp_path), "/grant", "*S-1-1-0:(OI)(CI)(R)"],
        check=True, capture_output=True,
    )
    replace = os.replace

    def check_acl(path):
        acl_file = tmp_path / "acl.txt"
        subprocess.run(
            ["icacls", str(path), "/save", str(acl_file)],
            check=True, capture_output=True,
        )
        sddl = acl_file.read_bytes().decode("utf-16-le")
        assert "D:P" in sddl
        assert ";;;WD)" not in sddl
        assert ";;;S-1-1-0)" not in sddl

    def checked_replace(source, destination):
        check_acl(source)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", checked_replace)
    write_config(target, 'api_key = "test-key"\n')
    check_acl(target)
    assert "test-key" in target.read_text(encoding="utf-8")
