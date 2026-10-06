import sys

import pytest

from redmine_mcp.setup.fsops import tighten_permissions, write_config


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


def test_權限收緊成功時回傳_None(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")
    assert tighten_permissions(target, lambda cmd: 0) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 才走 icacls")
def test_權限收緊失敗回傳原因而不是拋出(tmp_path):
    # 這條測試假設走 Windows 的 icacls 分支（回傳值取自 run() 的 exit code）；
    # 在 POSIX 上 tighten_permissions 走 path.chmod(0o600) 且完全不呼叫 run，
    # 會回傳 None 而讓下面的斷言失敗，因此限制只在 Windows 執行。
    # 檔案已經寫好且可用，權限收緊失敗不該讓整個安裝前功盡棄——
    # 回傳原因讓精靈提醒使用者自己處理即可。
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")
    reason = tighten_permissions(target, lambda cmd: 5)
    assert reason is not None
    assert "5" in reason


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 才走 icacls")
def test_授權對象帶上網域避免裸帳號被解析成別的主體(tmp_path, monkeypatch):
    """實測踩過的坑：只給裸帳號名時，icacls 可能解析到非本人的主體。

    在一台電腦名稱與帳號名稱相同的機器上（本機實測 whoami 為 kenny\\kenny），
    `icacls <檔> /inheritance:r /grant:r kenny:(R)` 會回傳 0，但產生的 DACL 是
    `KENNY\\:(R)`——沒有任何一條符合執行中的使用者，於是連本人都讀不到這個檔，
    server 下次啟動就會因為讀不到參數檔而失敗。改帶 USERDOMAIN 就不會有歧義。
    """
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("USERDOMAIN", "MYPC")
    monkeypatch.setenv("USERNAME", "kenny")
    收到: list[list[str]] = []

    def run(argv: list[str]) -> int:
        收到.append(argv)
        return 0

    assert tighten_permissions(target, run) is None
    assert 收到[0][-1] == "MYPC\\kenny:(R)"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 才走 icacls")
def test_沒有_USERDOMAIN_時退回裸帳號(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")
    monkeypatch.delenv("USERDOMAIN", raising=False)
    monkeypatch.setenv("USERNAME", "kenny")
    收到: list[list[str]] = []

    def run(argv: list[str]) -> int:
        收到.append(argv)
        return 0

    assert tighten_permissions(target, run) is None
    assert 收到[0][-1] == "kenny:(R)"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 才走 icacls 的回讀驗證")
def test_收緊後讀不到檔案要當成失敗回報(tmp_path):
    """icacls 回傳 0 不代表結果正確——實測過「成功收緊成本人也讀不到」。

    這道回讀檢查是本函式唯一能分辨「真的收緊了」與「把自己鎖在門外」的手段；
    後者比權限沒收緊嚴重得多（server 會直接讀不到參數檔），必須讓呼叫端知道。
    只在 win32 有意義：POSIX 走 path.chmod(0o600)、完全不呼叫 run，也沒有回讀，
    此測試注入的 run（模擬回讀失敗）在 POSIX 上永遠不會被呼叫。與同檔其餘三個
    icacls 路徑測試（權限收緊失敗、帶網域、退回裸帳號）的 skipif 一致。
    """
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")

    def run(argv: list[str]) -> int:
        target.unlink()  # 模擬「收緊後檔案變成讀不到」
        return 0

    reason = tighten_permissions(target, run)
    assert reason is not None
    assert "讀" in reason


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 才走 chmod")
def test_posix_直接改權限不呼叫外部指令(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")

    def 不該被呼叫(cmd):
        raise AssertionError("POSIX 不應呼叫外部指令")

    assert tighten_permissions(target, 不該被呼叫) is None
    assert oct(target.stat().st_mode)[-3:] == "600"
