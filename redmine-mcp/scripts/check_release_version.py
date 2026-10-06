"""發佈前的版本一致性檢查：確認 git tag 與 pyproject.toml 的版本相符。

由 .github/workflows/redmine-mcp-release.yml 在建立 Release 之前呼叫。

抽成獨立模組而不是寫在 workflow 的 shell 裡，是因為 tag 解析有好幾個分支
（前綴、段數、非數字），寫在 YAML 中只能靠實際打 tag 才驗得到，而打錯的代價
是發出一個「標示 v0.2.0、裡面 metadata 卻是 0.1.0」的 release——使用者裝下去
看到的是後者，事後只能刪 tag 重來。

用法：
    python scripts/check_release_version.py <tag> [pyproject 路徑]
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

#: monorepo 中每個工具各自發版，tag 必須帶套件前綴才不會互相撞。
#: 不用斜線（redmine-mcp/v0.1.0）是為了讓 git+<url>@<tag> 的安裝網址不必跳脫。
TAG_PREFIX = "redmine-mcp-v"

#: GitHub Actions 的 github.ref 會帶這段，github.ref_name 則不會；兩種都收。
_REF_PREFIX = "refs/tags/"


def parse_tag_version(tag: str) -> str:
    """從 git tag 取出版本號，格式不符就拋錯。

    只接受 `redmine-mcp-vX.Y.Z` 三段式數字版本。刻意不支援 rc／beta 等
    prerelease 後綴——目前沒有這個流程，放寬會讓「什麼算正式版」變得模糊。

    參數:
        tag: git tag 名稱，可帶 refs/tags/ 前綴。

    回傳:
        版本號字串，例如 "0.1.0"。
    """
    name = tag.removeprefix(_REF_PREFIX)
    if not name.startswith(TAG_PREFIX) or name == TAG_PREFIX:
        raise ValueError(
            f"tag 格式不符（收到：{tag!r}）；應為 {TAG_PREFIX}X.Y.Z，例如 {TAG_PREFIX}0.1.0"
        )
    version = name[len(TAG_PREFIX) :]
    # 用 [0-9] 而非 \d：\d 是 Unicode-aware，會放行全形數字（０.１.０），
    # 那種 tag 打得出來、卻永遠對不上 pyproject 的半形版本號。
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isascii() and part.isdigit() for part in parts):
        raise ValueError(
            f"版本段必須是三段半形數字 X.Y.Z（收到：{version!r}）；"
            f"完整格式為 {TAG_PREFIX}X.Y.Z"
        )
    return version


def read_pyproject_version(text: str) -> str:
    """從 pyproject.toml 內容取出 project.version。

    參數:
        text: pyproject.toml 的完整內容。

    回傳:
        版本號字串。
    """
    data = tomllib.loads(text)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml 讀不到 project.version")
    return version


def check(tag: str, pyproject_text: str) -> str:
    """比對 tag 與 pyproject.toml 的版本，相符時回傳該版本。

    參數:
        tag: git tag 名稱。
        pyproject_text: pyproject.toml 的完整內容。

    回傳:
        兩邊一致的版本號。
    """
    tag_version = parse_tag_version(tag)
    project_version = read_pyproject_version(pyproject_text)
    if tag_version != project_version:
        raise ValueError(
            f"版本不一致：tag 是 {tag_version}，pyproject.toml 是 {project_version}。"
            "請先修正其中一邊——改 pyproject 要重新 commit 並移動 tag，"
            "改 tag 則直接刪掉重打。"
        )
    return tag_version


def main(argv: list[str] | None = None) -> int:
    """命令列進入點；相符回傳 0，任何問題回傳 1 並把原因寫到 stderr。

    參數:
        argv: 命令列參數（不含程式名）；None 時取 sys.argv[1:]。

    回傳:
        行程結束碼。
    """
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("用法：check_release_version.py <tag> [pyproject 路徑]", file=sys.stderr)
        return 2
    tag = args[0]
    default_pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = Path(args[1]) if len(args) > 1 else default_pyproject
    try:
        version = check(tag, pyproject.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"版本檢查失敗：{exc}", file=sys.stderr)
        return 1
    print(f"版本一致：{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
