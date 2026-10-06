#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「要查核的程式範圍」轉成以 Redmine 單號分組的 git 事實 JSON。

用途說明：
    查核單號與程式異動是否一致時，最容易出錯的一步是「用 commit 標題當成事實」。
    標題只是開發者的宣稱，真正的事實是 diff。這支腳本負責把範圍解析、單號分組、
    檔案統計這些機械性的工作一次做完（三種範圍模式的 git 指令組合每次都一樣，
    人工拼容易漏 merge commit 或漏掉沒帶單號的 commit），讓後續的判讀可以
    專心在「這些異動有沒有滿足單子的要求」上。

三種範圍模式（對應 skill 步驟 1 詢問使用者的結果）：
    分支差異        --range master..HEAD
    單號反查 commit --issues 94206,94197
    指定 commit     --commits d1998e89,3321b58c

模式可疊加：--range 加上 --issues 代表「只看這個分支範圍內、屬於這幾張單的 commit」，
其餘單號會被列進 out_of_scope_issues 供反向查核使用。

使用範例：
    py collect_issue_changes.py --repo . --range master..HEAD
    py collect_issue_changes.py --repo . --issues 94206
    py collect_issue_changes.py --repo . --range master..HEAD --issues 94206 --with-diff

輸出：JSON（stdout，UTF-8），結構見檔尾 build_output() 的說明。
"""

import argparse
import io
import json
import re
import subprocess
import sys

# 從 commit 標題抓單號。公司慣例是標題開頭 "#94206 ..."（見 CLAUDE.md 的 commit 規範），
# 但 cherry-pick／merge 後單號可能落在中段，所以不綁行首。
ISSUE_RE = re.compile(r"#(\d{3,6})")

# 佔位單號：#0000 慣例上代表「沒有對應單號的雜項」，不該被當成真的 Redmine 單去查。
PLACEHOLDER_ISSUES = {"0000"}

# git 的 numstat 對二進位檔會輸出 "-"，需要區別於 0 行變更。
BINARY_MARK = "-"

FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"


# 舊 commit 可能是用 Big5 環境提交的，訊息不是 UTF-8。整份輸出強制 UTF-8 解碼會把
# 那些中文標題變成亂碼，而查核報告最需要看懂的就是標題，所以逐段嘗試備援編碼。
FALLBACK_ENCODINGS = ["utf-8", "cp950", "big5", "cp1252"]


def smart_decode(raw):
    """逐一嘗試常見編碼解碼 bytes，全部失敗才退回替換字元。"""
    for enc in FALLBACK_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def run_git_bytes(repo, args, allow_fail=False):
    """執行 git 指令並回傳原始 bytes。

    統一走這裡而不是各處自己 subprocess，是為了讓錯誤處理一致，
    並且把解碼時機留給呼叫端——混合編碼的 commit 訊息必須逐段解碼才不會壞掉。
    """
    proc = subprocess.run(["git", "-C", repo] + args, capture_output=True)
    if proc.returncode != 0:
        msg = smart_decode(proc.stderr).strip()
        if allow_fail:
            return b""
        raise SystemExit(f"git {' '.join(args)} 失敗：{msg}")
    return proc.stdout


def run_git(repo, args, allow_fail=False):
    """執行 git 指令並回傳解碼後的字串。"""
    return smart_decode(run_git_bytes(repo, args, allow_fail))


def parse_issues(subject, body):
    """從 commit 標題與內文抽出單號，過濾佔位單號。"""
    found = ISSUE_RE.findall(subject) + ISSUE_RE.findall(body)
    seen = []
    for num in found:
        if num in PLACEHOLDER_ISSUES:
            continue
        if num not in seen:
            seen.append(num)
    return seen


def parse_file_block(block):
    """解析單一 commit 的檔案區塊（--raw 的狀態列 + --numstat 的行數列）。"""
    status_map = {}
    stats = []
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith(":"):
            # raw 格式：:100644 100644 <old> <new> M\tpath（改名時為 R100\told\tnew）
            cols = line.split("\t")
            code = cols[0].rsplit(" ", 1)[-1][:1]
            status_map[cols[-1]] = code
            continue
        cols = line.split("\t")
        if len(cols) >= 3:
            stats.append((cols[0], cols[1], cols[-1]))

    files = []
    for added, deleted, path in stats:
        files.append({
            "path": path,
            "status": status_map.get(path, "M"),
            "added": None if added == BINARY_MARK else int(added),
            "deleted": None if deleted == BINARY_MARK else int(deleted),
        })
    return files


def list_commits(repo, rev_args, max_commits=None):
    """一次 git log 取回 commit 中繼資料與檔案異動。

    刻意不用「先 log 再逐 commit git show」的作法：那會對每個 commit 多開兩個
    子行程，範圍一大（分支落後主線數百個 commit 是常態）就會慢到不可用。
    這裡用 --raw --numstat 讓 git 一次吐完，再靠 \\x1e 分隔符切開每個 commit。

    merge commit 以 --no-merges 排除（本身沒有實質 diff），但呼叫端會另外數它們的數量，
    因為「這個範圍是靠 merge 帶進來的」會影響範圍是否完整的判斷。
    """
    fmt = RECORD_SEP + FIELD_SEP.join(["%H", "%h", "%an", "%ad", "%s", "%b"]) + RECORD_SEP
    cmd = ["log", "--no-merges", f"--pretty=format:{fmt}", "--date=short", "--raw", "--numstat", "-M"]
    if max_commits:
        cmd += [f"-n{max_commits}"]
    raw = run_git_bytes(repo, cmd + rev_args)

    # 逐段解碼：同一個 repo 裡 UTF-8 與 Big5 的 commit 訊息可能並存，
    # 整份一起解會讓其中一種變亂碼。
    chunks = [smart_decode(chunk) for chunk in raw.split(RECORD_SEP.encode("ascii"))]
    commits = []
    # chunks 依序為 [前綴空字串, meta1, files1, meta2, files2, ...]
    for i in range(1, len(chunks), 2):
        parts = chunks[i].split(FIELD_SEP)
        if len(parts) < 6:
            continue
        full, short, author, date, subject, body = parts[:6]
        block = chunks[i + 1] if i + 1 < len(chunks) else ""
        commits.append({
            "hash": short,
            "full_hash": full,
            "author": author,
            "date": date,
            "subject": subject,
            "body": body.strip(),
            "issues": parse_issues(subject, body),
            "files": parse_file_block(block),
        })
    return commits


def count_merges(repo, rev_args):
    """數範圍內的 merge commit，用來提醒呼叫端範圍可能不完整。"""
    out = run_git(repo, ["log", "--merges", "--pretty=format:%h"] + rev_args, allow_fail=True)
    return len([line for line in out.splitlines() if line.strip()])


def get_diff(repo, commit, path, max_lines):
    """取單一檔案在單一 commit 的 patch，超長時截斷。

    截斷而非全量輸出，是因為查核需要的是「改了什麼語意」，不是整份檔案。
    一次塞進上萬行 diff 只會把真正的判斷點淹掉。
    """
    out = run_git(repo, [
        "show", "--format=", "-M", commit["full_hash"], "--", path
    ], allow_fail=True)
    lines = out.splitlines()
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    return {"patch": "\n".join(lines), "truncated": truncated, "total_lines": len(out.splitlines())}


def resolve_commits(repo, args):
    """依三種範圍模式解析出 commit 清單，回傳 (commits, mode, spec, merge_count)。"""
    if args.commits:
        wanted = [c.strip() for c in args.commits.split(",") if c.strip()]
        commits = []
        for ref in wanted:
            found = list_commits(repo, ["-1", ref])
            if not found:
                raise SystemExit(f"找不到 commit：{ref}")
            commits.extend(found)
        return commits, "commits", ",".join(wanted), 0

    issue_filter = [i.strip().lstrip("#") for i in args.issues.split(",")] if args.issues else []

    if args.range:
        rev_args = [args.range]
        commits = list_commits(repo, rev_args, args.max_commits)
        merges = count_merges(repo, rev_args)
        mode = "range+issues" if issue_filter else "range"
        spec = args.range + (f" 限定單號 {','.join(issue_filter)}" if issue_filter else "")
        return commits, mode, spec, merges

    if issue_filter:
        # 沒給範圍時搜全歷史所有分支：單號反查的情境下，使用者往往不知道
        # 那張單的 commit 落在哪個分支（多租戶分支結構下尤其常見）。
        commits = []
        seen = set()
        for num in issue_filter:
            for c in list_commits(repo, ["--all", f"--grep=#{num}"], args.max_commits):
                if c["full_hash"] in seen:
                    continue
                seen.add(c["full_hash"])
                commits.append(c)
        return commits, "issues", ",".join(issue_filter), 0

    raise SystemExit("請至少指定 --range、--issues 或 --commits 其中一種範圍")


def build_output(repo, args, commits, mode, spec, merge_count):
    """把 commit 清單依單號分組並彙整檔案層級統計。

    輸出結構：
        mode / spec / merge_commits   範圍資訊，spec 原樣回填讓報告可引用
        issues[單號].commits          該單的 commit（含 files）
        issues[單號].files            該單所有 commit 合併後的檔案清單（去重、累加行數）
        unlabeled                     標題沒帶單號的 commit：一樣會進正式環境，不可略過
        out_of_scope_issues           範圍內出現、但不在 --issues 指定清單中的單號
    """
    issue_filter = [i.strip().lstrip("#") for i in args.issues.split(",")] if args.issues else []

    issues = {}
    unlabeled = []
    out_of_scope = {}

    for commit in commits:
        nums = commit["issues"]
        if not nums:
            unlabeled.append(commit)
            continue
        for num in nums:
            if issue_filter and num not in issue_filter:
                out_of_scope.setdefault(num, []).append(commit["hash"])
                continue
            issues.setdefault(num, {"commits": [], "files": []})["commits"].append(commit)

    # 指定了單號卻在範圍內完全找不到 commit 時，仍要建立空群組——
    # 「這張單在這個範圍裡沒有任何程式異動」本身就是查核結果，不能靜靜消失。
    for num in issue_filter:
        issues.setdefault(num, {"commits": [], "files": []})

    for num, group in issues.items():
        merged = {}
        for commit in group["commits"]:
            for f in commit["files"]:
                entry = merged.setdefault(f["path"], {
                    "path": f["path"],
                    "status": f["status"],
                    "added": 0,
                    "deleted": 0,
                    "commits": [],
                })
                if f["added"] is not None:
                    entry["added"] += f["added"]
                if f["deleted"] is not None:
                    entry["deleted"] += f["deleted"]
                if commit["hash"] not in entry["commits"]:
                    entry["commits"].append(commit["hash"])
        group["files"] = sorted(merged.values(), key=lambda x: x["path"])

    result = {
        "repo": repo,
        "mode": mode,
        "spec": spec,
        "merge_commits": merge_count,
        "issues": issues,
        "unlabeled": unlabeled,
        "out_of_scope_issues": out_of_scope,
        "summary": {
            "issue_count": len(issues),
            "commit_count": len(commits),
            "unlabeled_count": len(unlabeled),
        },
    }
    return result


def add_bounded_diffs(repo, output, max_lines, total_lines, total_chars=48000):
    """按實際輸出位置計算預算；多單號共用 commit 的 patch 不重複輸出。"""
    remaining = total_lines
    remaining_chars = total_chars
    seen = set()
    groups = [*output["issues"].values(), {"commits": output["unlabeled"]}]
    for group in groups:
        for commit in group["commits"]:
            for f in commit["files"]:
                key = (commit["full_hash"], f["path"])
                if key in seen:
                    f["diff"] = {"omitted": "duplicate", "source_commit": commit["hash"]}
                elif remaining <= 0 or remaining_chars <= 0:
                    f["diff"] = {"omitted": "total_budget", "truncated": True}
                else:
                    f["diff"] = get_diff(repo, commit, f["path"], min(max_lines, remaining))
                    patch = f["diff"]["patch"]
                    if len(patch) > remaining_chars:
                        f["diff"]["patch"] = patch[:remaining_chars]
                        f["diff"]["truncated"] = True
                    remaining_chars -= len(f["diff"]["patch"])
                    remaining -= len(f["diff"]["patch"].splitlines())
                seen.add(key)
    output["diff_budget"] = {
        "max_total_lines": total_lines, "emitted_lines": total_lines - remaining,
        "max_total_chars": total_chars, "emitted_chars": total_chars - remaining_chars,
    }


def main():
    parser = argparse.ArgumentParser(description="依 Redmine 單號彙整 git 異動事實，供一致性查核使用")
    parser.add_argument("--repo", default=".", help="git repo 路徑（預設當前目錄）")
    parser.add_argument("--range", help="分支或 commit 範圍，例如 master..HEAD")
    parser.add_argument("--issues", help="單號清單，逗號分隔，例如 94206,94197")
    parser.add_argument("--commits", help="commit 清單，逗號分隔")
    parser.add_argument("--max-commits", type=int, help="commit 數量上限，用於範圍過大時先探勘")
    parser.add_argument("--with-diff", action="store_true", help="附上各檔案的 patch 內容")
    parser.add_argument("--max-diff-lines", type=int, default=400, help="每個檔案 patch 的行數上限（預設 400）")
    parser.add_argument("--max-total-diff-lines", type=int, default=1200, help="整批 patch 行數上限（預設 1200）")
    parser.add_argument("--max-total-diff-chars", type=int, default=48000,
                        help="整批 patch 字元上限（預設 48000）")
    parser.add_argument("--pretty", action="store_true", help="縮排 JSON，預設輸出緊湊 JSON")
    args = parser.parse_args()
    if min(args.max_diff_lines, args.max_total_diff_lines, args.max_total_diff_chars) < 1:
        parser.error("diff 行數上限必須大於 0")

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")

    commits, mode, spec, merge_count = resolve_commits(args.repo, args)

    output = build_output(args.repo, args, commits, mode, spec, merge_count)
    if args.with_diff:
        # 分組可能共用同一物件；複製後才能對每個輸出位置獨立標註。
        output = json.loads(json.dumps(output))
        add_bounded_diffs(args.repo, output, args.max_diff_lines,
                          args.max_total_diff_lines, args.max_total_diff_chars)
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None,
                     separators=None if args.pretty else (",", ":")))


if __name__ == "__main__":
    main()
