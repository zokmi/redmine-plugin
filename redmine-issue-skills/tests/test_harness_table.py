"""harnesses.tsv 的結構驗證。

這張表是 install.sh 與 install.ps1 共用的唯一資料來源。表壞了兩支腳本一起壞，
所以先驗表，再驗腳本行為。
"""
from __future__ import annotations

from pathlib import Path

#: 偵測表。與 SKILL.md 同一層。
TABLE = Path(__file__).resolve().parents[1] / "harnesses.tsv"

#: 合法的類別。
#: native   —— 有原生 skill 機制，裝到自己的目錄，零侵入。
#: fallback —— 有可寫入的 Markdown 全域指示檔，走一行指標（需標記界定）。
#: manual   —— 偵測得到但沒有可寫入的 Markdown 指示檔，只印路徑讓使用者自己接。
VALID_KINDS = {"native", "fallback", "manual"}


def rows() -> list[list[str]]:
    """讀出偵測表的資料列（跳過註解與空行）。

    回傳:
        每列五個欄位的字串清單。
    """
    out: list[list[str]] = []
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        out.append(line.split("\t"))
    return out


def test_每列都是五欄():
    # 欄數不對會讓 bash 的 IFS 拆解錯位，而錯位不會報錯，只會靜默偵測不到。
    for row in rows():
        assert len(row) == 5, f"欄數應為 5，這列是 {len(row)}：{row}"


def test_類別只用三個合法值():
    for row in rows():
        assert row[3] in VALID_KINDS, f"{row[0]} 的類別 {row[3]!r} 不合法"


def test_id_不重複():
    ids = [row[0] for row in rows()]

    assert len(ids) == len(set(ids)), f"id 重複：{ids}"


def test_每列至少有一種偵測訊號():
    # 家目錄標記與 CLI 名兩欄都空，這列永遠偵測不到，等於白寫。
    for row in rows():
        assert row[1] or row[2], f"{row[0]} 沒有任何偵測訊號"


def test_fallback_必須有指示檔路徑():
    # fallback 的動作就是往指示檔寫一行；沒有路徑就無處可寫。
    for row in rows():
        if row[3] == "fallback":
            assert row[4], f"{row[0]} 是 fallback 但沒有指示檔路徑"


def test_native_包含_claude_code_與_codex():
    # Claude Code 與 Codex 都支援原生 skill 目錄，不應改走全域指示檔。
    native = [row[0] for row in rows() if row[3] == "native"]

    assert native == ["claude-code", "codex"], f"native 應包含 claude-code 與 codex，實際 {native}"
