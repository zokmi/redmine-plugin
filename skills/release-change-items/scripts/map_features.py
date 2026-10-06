#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把異動的檔案對應到前台實際的功能頁（路由），供判斷本次影響哪些重大功能。

為什麼用路由而不是資料夾名稱：
    重大功能檢核清單是給人照著點的測試腳本，「使用者會走到哪個畫面」才是有意義的單位。
    本專案的選單由資料庫驅動（menu.ts 只有型別定義，沒有靜態選單樹），
    所以 Angular routing module 是唯一可靠的靜態對應來源。

用法：
    py map_features.py --repo <repo路徑> --files-from changes.json
    py map_features.py --repo <repo路徑> --files CRMAG/src/app/pages/report/report9/report9.component.ts

輸出 JSON：
    routes    每個異動檔案對應到的前台路由（含所屬 app：CRMAG / SALEAG）
    unmapped  對應不到路由的檔案，通常是共用程式、後端或設定檔，需另行判斷影響範圍
"""

import argparse
import json
import os
import re
import sys

# 路由項目：{ path: 'report9', component: Report9Component }
ROUTE_RE = re.compile(
    r"path\s*:\s*['\"]([^'\"]*)['\"]\s*,\s*component\s*:\s*(\w+)", re.MULTILINE
)
# 具名匯入的來源路徑：import { Report9Component } from './report9/report9.component';
IMPORT_RE = re.compile(
    r"import\s*\{([^}]+)\}\s*from\s*['\"](\.[^'\"]+)['\"]", re.MULTILINE
)
# 延遲載入的子模組，用來還原上層路由前綴：
# { path: 'report', loadChildren: () => import('./pages/report/report.module') ... }
LAZY_RE = re.compile(
    r"path\s*:\s*['\"]([^'\"]*)['\"][^}]*?import\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE | re.DOTALL,
)

ROUTING_FILE_RE = re.compile(r"(routing\.module\.ts|(^|[/\\])routes\.ts)$", re.IGNORECASE)

# 只掃前端 app 目錄，避免走進 node_modules 拖慢速度。
SKIP_DIRS = {"node_modules", "dist", ".git", "bin", "obj", ".angular"}


def norm(path):
    """統一成正斜線的相對路徑，讓 Windows 與 git 輸出的路徑可以互相比對。"""
    return path.replace("\\", "/").lstrip("./")


def find_routing_files(repo):
    """找出 repo 內所有 Angular 路由檔。"""
    found = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            full = os.path.join(root, name)
            if ROUTING_FILE_RE.search(full.replace("\\", "/")):
                found.append(full)
    return found


def parse_routing_file(repo, path):
    """解析單一路由檔，回傳 (元件目錄 -> 路由清單) 與延遲載入前綴對應。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return {}, {}

    base_dir = os.path.dirname(path)

    # 先建立 元件類別名 -> 檔案所在目錄
    component_dir = {}
    for names, rel in IMPORT_RE.findall(text):
        target_dir = norm(os.path.relpath(
            os.path.normpath(os.path.join(base_dir, os.path.dirname(rel))), repo
        ))
        for raw in names.split(","):
            name = raw.strip()
            if name.endswith("Component"):
                component_dir[name] = target_dir

    dir_routes = {}
    for route_path, component in ROUTE_RE.findall(text):
        target_dir = component_dir.get(component)
        if not target_dir:
            continue
        dir_routes.setdefault(target_dir, set()).add(route_path or "(預設頁)")

    # 延遲載入：模組檔所在目錄 -> 該模組底下所有路由的前綴
    lazy_prefix = {}
    for prefix, module_rel in LAZY_RE.findall(text):
        module_dir = norm(os.path.relpath(
            os.path.normpath(os.path.join(base_dir, os.path.dirname(module_rel))), repo
        ))
        lazy_prefix[module_dir] = prefix

    return dir_routes, lazy_prefix


def build_index(repo):
    """掃描整個 repo，建立「目錄 -> 前台路由」索引。"""
    dir_routes = {}
    lazy_prefix = {}
    for path in find_routing_files(repo):
        routes, prefixes = parse_routing_file(repo, path)
        for key, value in routes.items():
            dir_routes.setdefault(key, set()).update(value)
        lazy_prefix.update(prefixes)

    # 套用延遲載入前綴：目錄若位於某個延遲載入模組底下，路由要補上前綴才是完整網址。
    resolved = {}
    for directory, routes in dir_routes.items():
        prefix = ""
        best = -1
        for module_dir, module_prefix in lazy_prefix.items():
            if directory == module_dir or directory.startswith(module_dir + "/"):
                if len(module_dir) > best:
                    best = len(module_dir)
                    prefix = module_prefix
        full = sorted(
            f"/{prefix}/{route}".replace("//", "/") if prefix else f"/{route}"
            for route in routes
        )
        resolved[directory] = full
    return resolved


def app_of(path):
    """判斷檔案屬於哪個前端 app，因為兩個 app 的使用者與功能面完全不同。"""
    head = norm(path).split("/")[0]
    return head if head in {"CRMAG", "SALEAG"} else head


def container_dirs(index):
    """找出「容器型」路由目錄：本身是其他路由目錄的上層。

    例如 CRMAG/src/app/pages 掛著版面外框的預設路由，同時又是所有頁面的父目錄。
    若允許往上比對到它，shared/ 或其他非頁面資料夾就會被誤判成某個功能頁，
    讓「本次必檢」多出根本沒動到的項目——那比漏標更糟，會稀釋掉真正該看的重點。
    """
    return {
        outer for outer in index
        if any(inner != outer and inner.startswith(outer + "/") for inner in index)
    }


def map_files(index, files):
    """把異動檔案對應到路由；對應不到的另外歸類。"""
    containers = container_dirs(index)
    mapped = {}
    unmapped = []
    for raw in files:
        path = norm(raw)
        directory = os.path.dirname(path)
        # 精準命中（檔案就放在路由目錄下）一律採用；
        # 找不到時才往上找，但跳過容器型目錄，避免張冠李戴。
        hit = None
        probe = directory
        first = True
        while probe:
            if probe in index and (first or probe not in containers):
                hit = probe
                break
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
            first = False
        if hit:
            entry = mapped.setdefault(hit, {
                "app": app_of(path),
                "dir": hit,
                "routes": index[hit],
                "files": [],
            })
            entry["files"].append(path)
        else:
            unmapped.append(path)
    return list(mapped.values()), unmapped


def collect_files_from_changes(payload):
    """從 collect_changes.py 的輸出取出所有異動檔案路徑（去掉狀態前綴）。"""
    files = []
    for issue in payload.get("issues", []):
        for bucket in ("code", "config", "sql", "build"):
            for record in issue.get(bucket, []):
                # 格式為 "M path/to/file"
                parts = record.split(" ", 1)
                files.append(parts[1] if len(parts) == 2 else record)
    return files


def main():
    parser = argparse.ArgumentParser(
        description="把異動檔案對應到前台路由，供判斷影響之重大功能。"
    )
    parser.add_argument("--repo", default=".", help="repo 路徑。")
    parser.add_argument(
        "--files-from",
        help="collect_changes.py 產出的 JSON 檔路徑；用 - 表示從 stdin 讀。",
    )
    parser.add_argument("--files", nargs="*", default=[], help="直接列出檔案路徑。")
    parser.add_argument(
        "--list-all",
        action="store_true",
        help="只輸出完整的「目錄 -> 路由」索引，用來盤點系統有哪些功能頁。",
    )
    args = parser.parse_args()

    index = build_index(args.repo)

    if args.list_all:
        print(json.dumps(
            {"route_count": sum(len(v) for v in index.values()), "index": index},
            ensure_ascii=False, indent=1,
        ))
        return

    files = list(args.files)
    if args.files_from:
        raw = sys.stdin.read() if args.files_from == "-" else open(
            args.files_from, encoding="utf-8"
        ).read()
        files += collect_files_from_changes(json.loads(raw))

    if not files:
        sys.exit("請提供 --files 或 --files-from，或改用 --list-all。")

    mapped, unmapped = map_files(index, files)
    print(json.dumps({
        "repo": args.repo,
        "routes": sorted(mapped, key=lambda m: (m["app"], m["dir"])),
        "unmapped": sorted(set(unmapped)),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
