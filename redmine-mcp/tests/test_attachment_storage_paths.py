"""Automatic attachment storage roots and opaque site keys."""

import hashlib
from pathlib import Path

import pytest

from redmine_mcp.attachment_storage import paths


@pytest.mark.parametrize(
    ("platform", "variable", "suffix"),
    [
        ("win32", "LOCALAPPDATA", "local"),
        ("darwin", None, "Library/Application Support"),
        ("linux", "XDG_DATA_HOME", "xdg"),
    ],
)
def test_platform_storage_root(monkeypatch, tmp_path: Path, platform, variable, suffix):
    home = tmp_path / "home"
    monkeypatch.setattr(paths.sys, "platform", platform)
    monkeypatch.setattr(paths.Path, "home", lambda: home)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    if variable:
        base = tmp_path / suffix
        monkeypatch.setenv(variable, str(base))
    else:
        base = home / suffix
    expected = base / "redmine-mcp" / "attachments"
    assert paths.user_attachment_dir() == expected
    assert not expected.exists()


@pytest.mark.parametrize(
    ("platform", "variable", "value", "fallback"),
    [
        ("win32", "LOCALAPPDATA", None, "AppData/Local"),
        ("win32", "LOCALAPPDATA", "", "AppData/Local"),
        ("win32", "LOCALAPPDATA", "relative/data", "AppData/Local"),
        ("linux", "XDG_DATA_HOME", None, ".local/share"),
        ("linux", "XDG_DATA_HOME", "", ".local/share"),
        ("linux", "XDG_DATA_HOME", "relative/data", ".local/share"),
    ],
)
def test_missing_or_relative_environment_falls_back(monkeypatch, tmp_path: Path, platform, variable, value, fallback):
    home = tmp_path / "home"
    monkeypatch.setattr(paths.sys, "platform", platform)
    monkeypatch.setattr(paths.Path, "home", lambda: home)
    if value is None:
        monkeypatch.delenv(variable, raising=False)
    else:
        monkeypatch.setenv(variable, value)
    assert paths.user_attachment_dir() == home / fallback / "redmine-mcp" / "attachments"


def test_changing_cwd_does_not_change_storage_root(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.Path, "home", lambda: home)
    monkeypatch.setenv("XDG_DATA_HOME", "relative/data")
    original = paths.user_attachment_dir()
    monkeypatch.chdir(tmp_path)
    assert paths.user_attachment_dir() == original
    assert original == home / ".local/share/redmine-mcp/attachments"


def test_site_key_uses_full_sha256_of_normalized_url():
    canonical = "https://example.com/redmine"
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert paths.site_storage_key("HTTPS://EXAMPLE.COM:443/redmine///") == expected
    assert paths.site_storage_key("https://example.com/redmine") == expected
    assert len(expected) == 64
    assert expected.isascii() and expected.isalnum()


def test_site_key_keeps_subpath_and_nondefault_port_distinct():
    base = paths.site_storage_key("https://example.com/redmine")
    assert paths.site_storage_key("https://example.com/redmine2") != base
    assert paths.site_storage_key("https://example.com:8443/redmine") != base
    assert paths.site_storage_key("http://example.com/redmine") != base
    assert paths.site_storage_key("http://example.com:80/redmine") == paths.site_storage_key("http://example.com/redmine")


def test_ipv6_host_keeps_brackets_and_normalizes_default_port():
    expected = hashlib.sha256(b"https://[2001:db8::1]/redmine").hexdigest()
    assert paths.site_storage_key("https://[2001:DB8::1]:443/redmine/") == expected
    assert paths.site_storage_key("https://[2001:db8::1]:8443/redmine") != expected


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/redmine",
        "https://user@example.com/redmine",
        "https://@example.com/redmine",
        "https://example.com/redmine?",
        "https://example.com/redmine#",
        "https://example.com/redmine?api_key=secret",
        "https://example.com/redmine#secret",
        "https://example.com:bad/redmine",
        "https://example.com:99999/redmine",
        "ftp://example.com/redmine",
        "https:///redmine",
    ],
)
def test_invalid_url_is_rejected_without_echoing_secret(url):
    with pytest.raises(ValueError) as exc:
        paths.site_storage_key(url)
    assert "secret" not in str(exc.value)
