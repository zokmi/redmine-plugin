"""六份骨架的內容不變式。

這些不變式原本住在 redmine-mcp/tests/test_guide_tools.py，由 get_issue_guide
工具的測試順帶守住。骨架移出 Python 套件後工具消失，但不變式與載體無關，
必須在這裡繼續守著——尤其「去識別化」那一條防的是客戶名稱外洩。
"""
from __future__ import annotations

import pytest

from tests.conftest import GUIDE_PATHS, guide

#: 六組 (kind, stage)，供逐份檢查的測試使用。
ALL_GUIDES = list(GUIDE_PATHS)


@pytest.mark.parametrize(("kind", "stage"), ALL_GUIDES)
def test_指南不含_client_專屬字眼(kind: str, stage: str | None):
    # 這份文字可能被任何 harness 的 agent 讀到，不可留 Claude Code 專屬措辭。
    # 注意：這裡只放不涉及內部專案的字面常量。內部識別字（去識別化前的真實欄位名／
    # 資料表名／單號）絕對不能以字面常量寫進這支測試，否則測試檔本身（會進公開
    # GitHub repo）就會變成外洩管道。
    text = guide(kind, stage)

    for banned in ("mcp__redmine__", "ToolSearch", "references/example"):
        assert banned not in text, f"({kind}, {stage}) 不應出現 {banned}"


def test_完整版範例已去識別化():
    # 完整版的範例已刻意改寫為去識別化後的虛構名稱。這裡採正向斷言「應該出現去識別化
    # 後的名稱」，而非把真實內部識別字寫進測試檔——若日後有人不小心把舊的、含真實內部
    # 識別字的文字貼回去，這些虛構名稱就會消失，本測試就會失敗。
    text = guide("bug", "diagnose")

    for expected in ("cStepDone", "RecordService", "TblRecordDetail", "A 專案 #12341"):
        assert expected in text, f"完整版應保留去識別化後的名稱 {expected}"


@pytest.mark.parametrize(
    ("kind", "stage", "later"),
    [
        ("bug", "report", "## 資訊不足時：用問答補齊"),
        ("bug", "diagnose", "## 各章節怎麼寫"),
        ("change", None, "## 資訊不足時：用問答補齊"),
        ("change", "assess", "## 各章節怎麼寫"),
        ("feature", None, "## 資訊不足時：用問答補齊"),
        ("feature", "assess", "## 各章節怎麼寫"),
    ],
)
def test_自我檢查排在展開說明之前(kind: str, stage: str | None, later: str):
    # 自我檢查是全篇唯一具驗證功能的一段，排在文件最末最容易在長對話裡被截掉。
    # 它必須緊接在骨架之後、展開說明之前。
    text = guide(kind, stage)

    checklist = text.index("## 自我檢查")
    assert checklist < text.index(later), f"({kind}, {stage}) 的自我檢查排在 {later} 之後"
    assert checklist > text.index("## 內文骨架"), f"({kind}, {stage}) 的自我檢查排到骨架之前"


@pytest.mark.parametrize("kind", ["change", "feature"])
def test_評估模板要求事實而非推測(kind: str):
    # 評估註記最大的失真來源是模型把推理當事實寫進去。骨架必須明講判準，
    # 並且自我檢查要問得出「來源」，而不是只說「要是事實」——後者是自我認證。
    text = guide(kind, "assess")

    assert "## 下筆前：先把事實釘死" in text, f"{kind} 的註記模板缺少事實門檻章節"
    assert "可能會影響" in text, f"{kind} 的評估模板未點名推測句式"
    assert "從哪裡查到的" in text, f"{kind} 的自我檢查未要求講出事實來源"


@pytest.mark.parametrize(
    ("kind", "removed"),
    [
        ("change", ("## 影響範圍",)),
    ],
)
def test_概述骨架不含已移到註記的章節(kind: str, removed: tuple[str, ...]):
    # 影響範圍是「查得出來才寫得出來」的事實快照，留在註記；驗收標準與待確認會被
    # 修、被劃掉、被答覆，而註記改不了，所以那兩章一律留在概述。斷言帶 "## " 前綴，
    # 才不會誤中內文裡提到章節名的散句。
    text = guide(kind)

    for marker in removed:
        assert marker not in text, f"{kind} 的概述骨架不該再有 {marker}"
    # 指路句要留下，否則搬走的內容就沒有人知道該去哪裡寫。
    assert "assess" in text


def test_新需求概述承載評估章節且維持最新():
    # 新需求與調整單相反：評估內容留在概述，隨每次修改更新成最新狀態。少了這些
    # 章節，查到的相依與排除事項就無處可寫，只會散回註記裡。
    text = guide("feature")

    for marker in ("## 影響範圍與相依", "## 結構異動", "## 不在範圍內", "## 待確認"):
        assert marker in text, f"新需求的概述骨架缺少 {marker.strip()}"
    # 「活文件」這條規則是這次改動的核心；掉了就會有人繼續用註記堆過程資訊。
    assert "## 概述是活文件" in text, "新需求的概述骨架未說明概述要更新成最新狀態"
    assert "回頭更新概述" in text, "新需求的概述骨架未要求異動時回頭更新概述"


def test_新需求的資料表兩章落在_DB_子單的概述():
    # DB 這一層已經有自己的子單，資料表的事全部落在那張單上。這份模板原本說它是
    # 「註記」，與拆子單那節同一份內容有兩個落點——兩處遲早不同步，而讀的人不知道
    # 哪一份算數。需求面的異動仍然回母單概述，不寫成註記。
    text = guide("feature", "assess")

    for marker in ("## 涉及的資料表", "## 資料表關聯"):
        assert marker in text, f"新需求的資料表模板缺少 {marker.strip()}"
    assert "DB 子單的概述" in text, "新需求的資料表模板未把落點指到 DB 子單的概述"
    assert "新增註記" not in text, "新需求的資料表模板仍把落點寫成註記"
    for banned in (
        "\n## 結構異動\n",
        "## 影響範圍與相依",
        "## 不在範圍內",
        "## 待確認",
        "## 補充驗收標準",
    ):
        assert banned not in text, f"新需求的註記模板不該再有 {banned.strip()}"
    assert "更新概述" in text, "新需求的註記模板未指出需求面異動要回頭更新概述"


def test_回報骨架不含改由優先權承載的章節():
    # 急迫度由單子的「優先權」（priority_id）欄位承載。內文再留一節「影響與急迫度」
    # 就會有兩處要同步，而排序的人只看欄位——文字那份遲早過期且不會有東西報錯。
    text = guide("bug", "report")

    assert "## 影響與急迫度" not in text, "回報骨架不該再有 ## 影響與急迫度"
    # 章節搬走時一併帶走的兩件事實（優先權欄位只有一個等級值，承載不了），
    # 落點改為「問題描述」；掉了不會有任何東西報錯。
    assert "資料被寫錯或遺失" in text, "回報骨架未保留「資料是否受損」這件事實"
    assert "替代做法" in text, "回報骨架未保留「有無替代做法」這件事實"
    # 指路句要留下，否則搬走的內容就沒有人知道該去哪裡填。
    assert "priority_id" in text, "回報骨架未指出急迫度改填哪個欄位"


@pytest.mark.parametrize("kind", ["bug", "change", "feature"])
def test_概述骨架交代建單必填欄位(kind: str):
    # 概述骨架只涵蓋主題與內文，但建單還有兩個欄位漏了就開不成單，而且不會有東西
    # 報錯：預計完成日（自行推定會被當成需求方答應過的期限）、追蹤標籤與專案的 id
    # （各站體系獨立，沿用別站的數字會開到錯的地方）。
    text = guide(kind, "report" if kind == "bug" else None)

    assert "due_date" in text, f"{kind} 的概述骨架未交代預計完成日"
    assert "不要自行推定一個日期" in text, f"{kind} 的概述骨架未擋自行推定期限"
    assert "各站的 id 體系彼此獨立" in text, f"{kind} 的概述骨架未交代 id 不可跨站沿用"


@pytest.mark.parametrize(("kind", "stage"), ALL_GUIDES)
def test_骨架不點名已刪除的工具(kind: str, stage: str | None):
    # 骨架離開 redmine-mcp 之後 get_issue_guide 這個工具已經不存在。任何一處殘留
    # 都會叫沒裝 MCP 的 agent 去呼叫一個不存在的工具，而那不會有任何東西報錯。
    # 階段之間的指路一律改用檔案路徑（references/<單別>/<階段>.md）。
    text = guide(kind, stage)

    assert "get_issue_guide" not in text, f"({kind}, {stage}) 仍點名已刪除的 get_issue_guide"


@pytest.mark.parametrize(("kind", "stage"), ALL_GUIDES)
def test_骨架載體中立不假設自己住在_MCP_server_裡(kind: str, stage: str | None):
    # 這六份會被複製到 ~/.claude/skills/ 或 ~/.redmine-issue-guides/ 底下，讀到它
    # 的 agent 手上不一定有 redmine-mcp。「本 server」這種措辭預設了載體，還會與
    # SKILL.md 的交付規則（先印在對話裡、沒有上傳能力）互相衝突。
    text = guide(kind, stage)

    assert "本 server" not in text, f"({kind}, {stage}) 出現預設載體的措辭「本 server」"


#: 只有 redmine-mcp 才有的工具名／參數名。骨架裡出現任何一個，就等於叫沒裝 MCP 的
#: agent 去用一個它沒有的能力。`add_issue_note`／`upload_attachment` 是工具名，
#: `uploads` 是 create_issue／update_issue 的附件參數名。
MCP_ONLY_TERMS = ("add_issue_note", "upload_attachment", "uploads")


@pytest.mark.parametrize(("kind", "stage"), ALL_GUIDES)
def test_骨架不點名只有_MCP_才有的工具與參數(kind: str, stage: str | None):
    # 這幾個字面值比「本 server」更危險：它們不只是措辭預設了載體，而是明講
    # 「附件／註記已經由某個機制處理掉了」。只裝 skill 的 agent 讀到就不會告訴
    # 使用者自己把圖拖進 Redmine 的附件區——圖片就此消失，而且不會有任何東西
    # 報錯。SKILL.md〈交付〉說的是「這裡沒有上傳能力」，骨架必須與它一致。
    text = guide(kind, stage)

    for banned in MCP_ONLY_TERMS:
        assert banned not in text, f"({kind}, {stage}) 仍點名只有 MCP 才有的 {banned}"


@pytest.mark.parametrize("kind", ["change", "feature"])
def test_評估模板不自己重述落點(kind: str):
    # 落點由 SKILL.md 的對照表逐項列舉，是唯一事實來源。骨架內再寫一份〈落點〉
    # 章節就會有兩處要同步，其中一處寫錯不會有任何東西報錯。
    text = guide(kind, "assess")

    assert "## 落點" not in text, f"{kind} 的評估模板不該自己重述落點"


@pytest.mark.parametrize(
    ("kind", "stage"),
    [("bug", "diagnose"), ("change", "assess"), ("feature", "assess")],
)
def test_註記模板不承載驗收標準與待確認(kind: str, stage: str | None):
    # Redmine 的註記實務上只能新增：驗收標準會被勾、被調整，待確認會被答覆，兩者
    # 留在改不動的註記裡就只會愈積愈多份，而讀的人不知道哪一份算數。三份註記模板
    # 一律不放這兩章，查出來的判準與疑問回寫概述。
    text = guide(kind, stage)

    for banned in (
        "\n## 驗收標準\n",
        "\n## 補充驗收標準\n",
        "\n## 待確認\n",
        "\n## 待確認（需求面）\n",
    ):
        assert banned not in text, f"({kind}, {stage}) 不該再有 {banned.strip()}"
    assert "回頭更新概述" in text, f"({kind}, {stage}) 未指出查出來的內容要回寫概述"


def test_診斷模板把驗收判準與待確認交回概述():
    # 驗收的人只看概述。判準散在「概述一份、註記一份」時，他不會知道還有第二份要讀；
    # 需求面待拍板的事留在註記，也會被一則又一則的後續留言蓋過去。診斷查出來的這兩類
    # 內容一律回寫概述，註記只留機制、修正方向與參考。
    text = guide("bug", "diagnose")

    for banned in ("\n## 補充驗收標準\n", "\n## 待確認（需求面）\n"):
        assert banned not in text, f"診斷模板不該再有 {banned.strip()}"
    # 整份驗收標準也不該搬進註記——回寫的位置是概述那一份。
    assert "\n## 驗收標準\n" not in text, "診斷模板不該自己重寫一份驗收標準"
    # 指路句要留下，否則搬走的內容就沒有人知道該去哪裡寫。
    assert "## 診斷後要回頭更新概述" in text, "診斷模板未說明哪些內容要回寫概述"
    assert "回頭更新概述" in text, "診斷模板未要求把查出來的判準與待確認寫回概述"


def test_回報骨架預留診斷後回填的待確認章節():
    # 〈待確認〉多半由 RD 診斷後才寫得出來，但概述骨架若完全不提，回寫的人會不知道
    # 該加在哪、或乾脆留在註記裡。骨架先留位置並標明由誰追加，兩份文件才對得上。
    text = guide("bug", "report")

    assert "## 待確認" in text, "回報骨架未預留〈待確認〉的位置"
    assert "診斷後追加" in text, "回報骨架未說明〈待確認〉由 RD 診斷後追加"
def test_調整概述預留評估後回填的待確認章節():
    # 〈待確認〉多半由手上有事實的人評估後才寫得出來，但概述骨架若完全不提，回寫的
    # 人會不知道該加在哪、或乾脆留在改不動的註記裡。
    text = guide("change")

    assert "## 待確認" in text, "調整單概述未預留〈待確認〉的位置"
    assert "評估後追加" in text, "調整單概述未說明〈待確認〉由評估後追加"


def test_評估模板指出回寫概述的落點():
    # 兩份評估模板都要明講哪些內容回寫概述、寫進哪一章，否則搬走的內容就會不見。
    for kind, expected in (
        ("change", "## 評估後要回頭更新概述"),
        ("feature", "## 查到之後要回頭更新概述"),
    ):
        text = guide(kind, "assess")
        assert expected in text, f"{kind} 的評估模板缺少〈{expected.strip('# ')}〉"


def test_新需求概述要求新功能開發拆成三張子單():
    # 新功能開發若只開一張單，UI、API、DB 三層的工作會混在同一份驗收裡，誰做完了
    # 沒人分得出來。骨架必須明講：母單之外一律再開 UI／API／DB 三張子單。
    text = guide("feature")

    assert "## 新功能開發要拆子單" in text, "新需求的概述骨架未要求拆成子單"
    for layer in ("（UI）", "（API）", "（DB）"):
        assert layer in text, f"子單拆分那節缺少 {layer} 的主題格式"
    # 三張是下限而非上限——工作量大時還能再往下拆，但不能少於三張。
    assert "三張是下限" in text, "未說明三張子單是下限、可再往下拆"
    # 某一層這次不動時仍要開單，否則單數會浮動，回頭盤點時分不出「不用做」與「忘了開」。
    assert "本次不動" in text, "未交代某一層這次不動時該怎麼寫那張子單"
    # 措辭要載體中立：只裝 skill 的人是在 Redmine 網頁上填「父任務」欄位。
    assert "父任務" in text, "未交代子單要以父任務欄位掛在母單底下"
    assert "parent_issue_id" not in text, "骨架不該點名只有 MCP 才有的參數名"


def test_新需求母單不重述各層細節():
    # 母單與子單各寫一份細節就會有兩處要同步，而驗收的人不知道哪一份算數。
    # 母單留需求全貌與跨層驗收，各層細節只在該層子單。
    text = guide("feature")

    assert "跨層" in text, "未說明母單的驗收標準只留跨層條目"
    # 〈結構異動〉是 DB 那一層的細節。母單留一份、DB 子單再留一份，兩處遲早不同步。
    assert "〈結構異動〉整段移到 DB 子單" in text, "未說明結構異動要整段移到 DB 子單"


def test_DB_子單要求純文字資料表關聯圖():
    # 關聯只有條列時，讀的人要在腦裡拼出整張圖；但圖本身不精確，欄位對欄位仍得靠
    # 條列。兩者並存，且圖用純文字畫——不能假設 Redmine 裝了會算圖的外掛。
    text = guide("feature")

    assert "關聯圖" in text, "DB 子單未要求畫資料表關聯圖"
    assert "不可只有圖" in text, "未說明關聯圖不取代欄位對欄位的條列"
    assert "mermaid" not in text, "關聯圖不可依賴 Redmine 外掛才畫得出來"


def test_DB_子單以中文敘述描述結構而不貼_SQL():
    # 兩個後果：SQL 條件式對 PM 與需求方是雜訊；而部分站台的 WAF 會把 SQL 樣式當成
    # 注入攻擊攔掉，被攔時 Redmine 沒收到請求、工具只回一句 Error executing tool，
    # 看不到 403 也看不到原因。規則只寫在骨架文字裡，日後有人重寫那節把它刪掉不會
    # 有任何東西報錯——這條測試就是那個報錯的東西。
    text = guide("feature")

    assert "不要貼 SQL" in text, "DB 子單那節未要求改用中文敘述描述結構"
    assert "WAF" in text, "未說明部分站台的 WAF 會攔 SQL 樣式"
    # 對照表是這條規則唯一可照做的部分；只留一句「不要貼 SQL」，寫的人不知道改成什麼。
    for pattern in ("ALTER TABLE", "sp_addextendedproperty", "identity(1,1)"):
        assert pattern in text, f"改寫對照表缺少 {pattern} 這一列"
    # 反引號欄位名與 Markdown 表格照常使用；不講會被讀成整篇不准出現程式字樣。
    assert "都不受影響" in text, "未說明反引號欄位名與 Markdown 表格不受影響"
    assert "沒有 SQL 條件式" in text, "自我檢查沒有這一條"


def test_新需求資料表兩章不禁止關聯圖但條列仍是權威():
    # 原本「不要只畫一張看不懂的圖」會被讀成不准畫圖，與 DB 子單要求關聯圖相衝突。
    # 允許畫，但欄位對欄位的條列仍然是權威那一份。
    text = guide("feature", "assess")

    assert "不要只畫一張看不懂的圖" not in text, "資料表模板仍在禁止畫圖，與 DB 子單的要求相衝突"
    assert "條列" in text, "資料表模板未要求以條列寫出欄位對欄位的關聯"


def test_結構異動以表格逐欄位列出並帶中文說明():
    # 條列寫欄位時，型別、可否為空、預設值、說明四件事的順序每個人各寫一套，漏一項
    # 也看不出來。改成固定欄數的表格，缺格就是空白，一眼就發現。中文說明是給非 IT
    # 讀者與日後接手的人看的：欄位名看不出它裝什麼。
    text = guide("feature")

    header = "| 欄位 | 型別 | 可否為空 | 預設值 | 中文說明 |"
    assert header in text, "結構異動未改成逐欄位的表格"
    assert text.count(header) >= 2, "表格只出現一次：骨架與展開說明的範例應各有一份"
    assert "中文說明" in text, "表格未要求填中文欄位說明"
    # 既有資料怎麼補不是欄位層級的事實，仍要在表格外寫一句。
    assert "既有資料怎麼處理一定要寫" in text, "改成表格後漏掉既有資料的處理"
