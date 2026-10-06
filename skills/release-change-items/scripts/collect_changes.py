#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依 Redmine 單號彙整 git 異動檔案，並分類到過版檢核文件「版更項目」分頁的欄位。

用途說明：
    過版檢核文件的「版更項目」分頁需要逐單填寫「異動SQL／異動設定檔／異動程式」，
    而這三欄的事實來源是 git 實際變更的檔案，不是 Redmine 單上的文字描述
    （單子描述常常是空的）。這支腳本把 git log 的檔案清單依單號分組並歸類，
    讓後續只需要專注在「這些異動代表什麼、影響哪些重大功能」的判讀上。

使用範例：
    python collect_changes.py --repo . --range main..HEAD
    python collect_changes.py --repo . --range main..release --issues 4590,4432
    python collect_changes.py --repo . --issues 4590            # 全歷史搜尋該單號

輸出：JSON（stdout），結構見 --help 或 references/schemas 說明。
"""

import argparse
import json
import re
import subprocess
import sys

# 從 commit 標題抓單號。公司慣例為標題開頭 "#4590 ..."，
# 但也可能出現在中段（例如 cherry-pick 或 merge 後補述），故不綁定行首。
ISSUE_RE = re.compile(r"#(\d{3,6})")

# 佔位單號：#0000 代表「無對應單號的雜項修正」，不應被當成真實 Redmine 單去查詢。
PLACEHOLDER_ISSUES = {"0000"}

# 檔案分類規則。順序有意義：由最專一到最寬鬆，第一個命中的即為分類結果。
# 之所以要把文件類單獨拉出來，是因為 .md／設計文件不影響正式環境行為，
# 填進「異動程式」只會讓複檢人員多花時間確認無關項目。
CLASSIFY_RULES = [
    ("sql", [r"\.sql$"]),
    ("config", [
        r"appsettings[^/\\]*\.json$",
        r"web\.config$",
        r"environments[/\\][^/\\]+\.ts$",
        r"(^|[/\\])\.env",
        r"nginx[^/\\]*\.conf$",
        r"[/\\]Dockerfile$",
        r"docker-compose[^/\\]*\.ya?ml$",
    ]),
    ("doc", [
        r"\.md$",
        r"^docs[/\\]",
        r"^openspec[/\\]",
        r"^說明文件[/\\]",
        r"[/\\]docs[/\\]",
    ]),
    ("build", [
        r"package(-lock)?\.json$",
        r"\.csproj$",
        r"\.sln$",
        r"angular\.json$",
        r"tsconfig[^/\\]*\.json$",
    ]),
]


def classify(path):
    """判斷單一檔案路徑屬於哪一類（sql / config / doc / build / code）。"""
    for bucket, patterns in CLASSIFY_RULES:
        for pattern in patterns:
            if re.search(pattern, path, re.IGNORECASE):
                return bucket
    return "code"


def run_git(repo, args):
    """在指定 repo 執行 git 指令並回傳 stdout 文字。"""
    result = subprocess.run(
        ["git", "-C", repo] + args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(f"git 指令失敗：git {' '.join(args)}")
    return result.stdout


def parse_log(repo, rev_range, grep_issue=None):
    """解析 git log，回傳 commit 清單（含檔案異動狀態）。

    以 \x01 當欄位分隔、\x02 當 commit 分隔，避免 commit 標題含中文標點時被誤切。
    """
    args = [
        "log",
        "--no-merges",
        "--name-status",
        "--date=short",
        "--pretty=format:\x02%H\x01%s\x01%an\x01%ad",
    ]
    if grep_issue:
        args += ["--grep", f"#{grep_issue}"]
    if rev_range:
        args.append(rev_range)

    raw = run_git(repo, args)
    commits = []
    for block in raw.split("\x02"):
        block = block.strip("\n")
        if not block:
            continue
        head, _, body = block.partition("\n")
        parts = head.split("\x01")
        if len(parts) < 4:
            continue
        sha, subject, author, date = parts[:4]
        files = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            cols = line.split("\t")
            if len(cols) < 2:
                continue
            status = cols[0]
            # 改名（R100）會有兩個路徑欄位，取新路徑才是版更後實際存在的檔案。
            path = cols[-1]
            files.append({"status": status, "path": path})
        commits.append({
            "sha": sha[:8],
            "subject": subject,
            "author": author,
            "date": date,
            "files": files,
        })
    return commits


def group_by_issue(commits, wanted=None):
    """把 commit 依單號分組，並把檔案歸類到各欄位對應的桶子。"""
    groups = {}
    for commit in commits:
        found = ISSUE_RE.findall(commit["subject"])
        # 一個 commit 標題可能帶多個單號；沒帶號的歸到 "未標號"，
        # 這類異動仍會進正式環境，必須讓人看見而不是默默丟掉。
        issues = [n for n in found if n not in PLACEHOLDER_ISSUES] or ["未標號"]
        for issue in issues:
            if wanted and issue not in wanted:
                continue
            group = groups.setdefault(issue, {
                "issue": issue,
                "commits": [],
                "sql": [],
                "config": [],
                "code": [],
                "doc": [],
                "build": [],
            })
            group["commits"].append(
                f"{commit['sha']} {commit['date']} {commit['subject']}"
            )
            for entry in commit["files"]:
                bucket = classify(entry["path"])
                # 以 "狀態<空白>路徑" 的單行字串儲存，而非巢狀物件。
                # 一次過版動輒數百個檔案，精簡格式能大幅降低閱讀成本。
                record = f"{entry['status']} {entry['path']}"
                if record not in group[bucket]:
                    group[bucket].append(record)
    return groups


def main():
    parser = argparse.ArgumentParser(
        description="依 Redmine 單號彙整 git 異動，供過版檢核文件「版更項目」分頁填寫。"
    )
    parser.add_argument("--repo", default=".", help="git repo 路徑，預設為目前目錄。")
    parser.add_argument(
        "--range",
        dest="rev_range",
        default=None,
        help="git 版本範圍，例如 main..HEAD 或 v1.8..v1.9。省略時需搭配 --issues 全歷史搜尋。",
    )
    parser.add_argument(
        "--issues",
        default=None,
        help="只保留這些單號，以逗號分隔（例：4590,4432）。",
    )
    args = parser.parse_args()

    wanted = None
    if args.issues:
        wanted = {n.strip().lstrip("#") for n in args.issues.split(",") if n.strip()}

    if not args.rev_range and not wanted:
        sys.exit("請至少提供 --range 或 --issues 其中之一。")

    if args.rev_range:
        commits = parse_log(args.repo, args.rev_range)
    else:
        # 沒給範圍時逐一單號搜尋全歷史，避免掃出無關 commit。
        commits = []
        seen = set()
        for issue in sorted(wanted):
            for commit in parse_log(args.repo, None, grep_issue=issue):
                if commit["sha"] not in seen:
                    seen.add(commit["sha"])
                    commits.append(commit)

    groups = group_by_issue(commits, wanted)

    # 「未標號」排最後，其餘依單號數字排序，讓輸出順序穩定可預期。
    def sort_key(issue):
        return (1, 0) if issue == "未標號" else (0, int(issue))

    output = {
        "repo": args.repo,
        "range": args.rev_range,
        "issue_count": len(groups),
        "issues": [groups[k] for k in sorted(groups, key=sort_key)],
    }
    print(json.dumps(output, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
