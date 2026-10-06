"""專案工具：建立 Redmine 專案。

查詢專案清單請用 metadata 模組的 `list_projects`；這裡只放會寫入的操作。
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from redmine_mcp.formatting import neutralize_untrusted_markers
from redmine_mcp.sites import WRITE_SITE_DESCRIPTION, SiteRegistry

WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)

#: Redmine 對 identifier 的限制：1–100 字元，只能用小寫英文、數字、減號與底線，且需以英數開頭。
#: 中文名稱不能當 identifier，必須另外給一組英數代號（會出現在專案網址上）。
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


def validate_identifier(identifier: str) -> str:
    """驗證並回傳專案 identifier，不符合 Redmine 規則時直接拒絕。

    先在本地擋掉明顯不合法的值（例如中文、大寫、空白），
    可省下一次注定失敗的 API 往返，錯誤訊息也比 Redmine 的 422 更好懂。
    """
    candidate = identifier.strip()
    if not _IDENTIFIER_PATTERN.match(candidate):
        raise ValueError(
            "identifier 只能用小寫英文、數字、減號與底線，需以英數開頭且長度 1–100，"
            f"不接受中文或大寫（收到：{identifier!r}）"
        )
    return candidate


def register(mcp: Any, sites: SiteRegistry) -> None:
    """在 MCP server 上註冊專案工具。"""

    @mcp.tool(
        annotations=WRITE,
        description=(
            "在 Redmine 建立新專案。這會實際寫入 Redmine，且需要管理員或具建立專案權限的帳號。"
            "identifier 是專案網址代號，只能用小寫英數與 -_，中文請放在 name。"
            "預設建立為非公開專案（is_public=false）。"
        ),
    )
    async def create_project(
        name: Annotated[str, Field(description="專案名稱，可用中文（必填）。")],
        identifier: Annotated[
            str,
            Field(
                description="專案代號，出現在網址上，只能用小寫英數與 -_（必填、不可事後修改）。"
            ),
        ],
        site: Annotated[str | None, Field(description=WRITE_SITE_DESCRIPTION)] = None,
        description: Annotated[str | None, Field(description="專案說明。")] = None,
        homepage: Annotated[str | None, Field(description="專案首頁網址。")] = None,
        is_public: Annotated[
            bool, Field(description="是否為公開專案；預設 false（僅成員可見）。")
        ] = False,
        parent_id: Annotated[
            int | None, Field(description="父專案的數字 id，建立子專案時使用。")
        ] = None,
        inherit_members: Annotated[
            bool | None, Field(description="是否沿用父專案的成員設定。")
        ] = None,
        enabled_module_names: Annotated[
            list[str] | None,
            Field(
                description=(
                    "要啟用的模組名稱清單，例如 issue_tracking、time_tracking、wiki、documents；"
                    "未提供則使用站台預設。"
                )
            ),
        ] = None,
        tracker_ids: Annotated[
            list[int] | None,
            Field(description="要啟用的追蹤標籤 id 清單，可先用 list_trackers 查詢。"),
        ] = None,
    ) -> dict[str, Any]:
        """建立專案。

        參數:
            name: 專案名稱，可用中文（必填）。
            identifier: 專案代號，出現在網址上，只能用小寫英數與 -_（必填、不可事後修改）。
            site: 要寫入的站台代號；設定多個站台時必填。
            description: 專案說明。
            homepage: 專案首頁網址。
            is_public: 是否為公開專案；預設 false。
            parent_id: 父專案的數字 id。
            inherit_members: 是否沿用父專案的成員設定。
            enabled_module_names: 要啟用的模組名稱清單。
            tracker_ids: 要啟用的追蹤標籤 id 清單。
        """
        site_name, client = sites.resolve_for_write(site)
        if not name.strip():
            raise ValueError("name 不可為空白")
        payload: dict[str, Any] = {
            "name": name.strip(),
            "identifier": validate_identifier(identifier),
            "is_public": bool(is_public),
        }
        optional: dict[str, Any] = {
            "description": description,
            "homepage": homepage,
            "parent_id": parent_id,
            "inherit_members": inherit_members,
            "enabled_module_names": enabled_module_names,
            "tracker_ids": tracker_ids,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})

        raw = await client.post("/projects.json", {"project": payload})
        created = raw.get("project") or {}
        return {
            "site": site_name,
            "id": created.get("id"),
            # name 取自 Redmine 回應，屬使用者可控的參照名稱；與 list_projects
            # 的裁定一致，只中和圍籬標記、不包圍籬。
            "name": neutralize_untrusted_markers(str(created.get("name") or "")),
            "identifier": created.get("identifier") or payload["identifier"],
        }
