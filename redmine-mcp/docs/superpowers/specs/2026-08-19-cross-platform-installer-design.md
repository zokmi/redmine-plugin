# 跨平台一鍵安裝器（`install.ps1` / `install.sh` + `redmine-mcp install`）— 設計

日期：2026-08-19

範圍限定 `redmine-mcp` 一個套件。`llm-wiki-mcp` 與 `release-doc-mcp` 的安裝體驗
另案處理——它們目前連 `setup` 子命令的形狀都不一致，先把一個套件做到位再談統一。

## 問題

現行安裝是兩行指令：

```powershell
uv tool install --from "git+https://github.com/Shinspire/MCP.git#subdirectory=redmine-mcp" redmine-mcp
redmine-mcp setup
```

`redmine-mcp setup`（2026-08-17 的安裝精靈）已經把「建參數檔 → 驗金鑰 → 註冊到
Claude Code → 冒煙測試」收斂得很好。剩下的問題全部集中在**它之前與它之外**：

1. **第一行仍是最容易失敗的一步。** 那串 git URL 的陷阱一個都沒少：引號不可省
   （PowerShell 會把 `#` 之後當註解丟掉）、`#subdirectory=` 不可省。而且它假設
   `uv` 已經裝好了。
2. **前置套件要使用者自己張羅。** uv、Claude Code CLI 都得先自己裝，文件只寫了
   `winget install astral-sh.uv` 一句。
3. **沒有 Linux 路徑。** 現行文件通篇是 PowerShell。
4. **裝壞了沒有修復手段。** 只有「重跑 setup」，但 setup 只管參數檔，不管套件本身、
   不管註冊、不管前置。
5. **移除只有手動步驟**（README 列了兩行指令要使用者自己貼）。

目標：**一行指令，從什麼都沒有到 `/mcp` 顯示 connected 的前一步**，並提供升級、
修復、重裝、移除四種後續操作。

## 前提：repo 仍是 private

2026-08-19 實測（與 2026-08-17 的結論一致，條件未變）：

| 檢查 | 結果 |
| --- | --- |
| `raw.githubusercontent.com/Shinspire/MCP/main/README.md` | 404 |
| `api.github.com/repos/Shinspire/MCP` | 404 |
| `git clone` / `uv tool install --from git+https://...` | 成功（走本機 git 憑證） |

即 **git-over-HTTPS 通、匿名 HTTP 不通**。這否決了 `irm https://.../install.ps1 | iex`
式的託管安裝腳本——使用者連腳本本身都下載不到。

> **2026-08-19 補充：** 匿名不通，但**帶認證**的 HTTP 取檔是通的。`gh api
> repos/Shinspire/MCP/contents/install/install.sh -H 'Accept: application/vnd.github.raw'`
> 會以使用者自己的 GitHub 認證取回檔案內容（實測可行），因此
> `gh api ... | bash` 這種一行安裝是做得到的，只是把前置從 git 換成 gh。
> 兩種取得方式都保留：有 gh 用一行版，沒有就用 clone 版。

**因此安裝腳本只能由 `git clone` 或 `gh api` 取得（兩者都需認證）。** 這帶來一個必須講明白的推論：

> **git 是唯一無法由安裝器自動安裝的前置**，因為要先有 git（且憑證設定好、帳號有
> 這個 repo 的存取權）才拿得到安裝器本身。「前置全自動」的範圍是 git 以外的一切。

反過來，uv 與 Claude Code 的官方安裝端點都是公開的，2026-08-19 實測皆回 200：
`astral.sh/uv/install.sh`、`astral.sh/uv/install.ps1`、`claude.ai/install.sh`、
`claude.ai/install.ps1`。所以除了 git 之外的前置，安裝器都能自己處理。

## 前提：pycurl 在 Linux 不需要編譯

`redmine-mcp` 依賴 pycurl（以 libcurl 繞過站台 Cloudflare 對 Python ssl 的 TLS
指紋阻擋，見 `pycurl_transport.py`）。這原本是 Linux 安裝器最大的變數——pycurl 過去
只發 sdist，裝起來要 `libcurl4-openssl-dev` 與編譯器。

2026-08-19 查 PyPI：pycurl 7.47.0 提供 `manylinux_2_28` 的 x86_64 與 aarch64 wheel
（cp310–cp314），Windows 提供 `win_amd64` 與 `win_arm64` wheel。

**結論：Linux 與 Windows 皆不需要編譯工具鏈**，安裝器不必處理 `apt install
build-essential libcurl4-openssl-dev` 這類分支。

已知限制：Alpine 等 musl 系統沒有對應 wheel，會落到需要編譯。不在支援範圍內，
偵測到時明確告知而非讓 uv 的編譯錯誤直接噴出去。

## 已決定的取捨

| 決策 | 選擇 | 理由 |
| --- | --- | --- |
| 邏輯放哪 | **薄 shell bootstrap + 厚 Python** | 四種模式的邏輯只寫一份，可納入現有 pytest 體系；兩份 shell 壓到無分支邏輯，分歧成本最低 |
| 腳本位置 | `install/install.ps1`、`install/install.sh` | **2026-08-19 修訂**：原訂跟著套件走（`redmine-mcp/` 之下），改為 repo 根目錄的 `install/`。理由是一行安裝指令要引用一個穩定、與子套件無關的路徑；三個子套件共用同一份 bootstrap（它只負責裝 uv 與套件，套件名是唯一差異），沒有各留一份的必要 |
| 取得管道 | **`git clone` 暫存目錄** | private repo 的唯一可行路徑（見上節） |
| 暫存 clone | **用完即刪** | 裝完後 `redmine-mcp` 已在 PATH，維護模式不必再 clone |
| 前置套件 | **除 git 外全自動**，動系統層前問一次 | 使用者選定 |
| 進度呈現 | **分步驟文字進度**（`[3/6] ... ✓`） | 跨平台一致、cp950 與 CI log 都讀得懂；真進度條拿不到 uv／winget 的真實百分比，只會是估的 |
| 流程終點 | **裝完自動接上 `run_setup()`** | 使用者選定；金鑰仍走精靈的不回顯輸入，不進 argv |
| 後續操作 | 冪等重跑（升級）、`--repair`、`--reinstall`、`--uninstall` | 使用者選定，四種全做 |
| Claude Code CLI 的處理 | **在 Python 層而非 shell** | 它不是安裝套件的前提；放 Python 才問得出「要不要幫你裝」並印得出一致的進度 |
| 目標平台 | Windows 原生 + WSL 為主，原生 Linux 次要 | 使用者選定 |
| shell 單元測試 | **不寫**，改以 CI 端對端驗證 | 這正是把 shell 壓到三步無邏輯的理由；CI 實際跑一次比任何 shell 單元測試有價值 |

被否決的方案：

- **兩份完整的 shell 腳本。** 套件裝壞時也能修，是它唯一的優勢。代價是進度、四種模式、
  偵測邏輯各寫兩遍（估 300+ 行 ×2），且幾乎無法納入現有 pytest 體系——本 repo 的既有
  紀律是「有邏輯就要有單元測試」，兩份不可測的 shell 直接違背它。套件壞掉的情境改由
  「重跑 bootstrap」涵蓋，成本可接受。
- **不寫安裝器，只給一行複合指令**（`winget install astral-sh.uv; uv tool install ...;
  redmine-mcp setup`）。零新程式碼，但四項需求只滿足「從 git 安裝」一項：沒有進度、
  沒有修復與移除、失敗訊息是各工具原生的（uv 解析失敗的輸出對非工程師不可讀）。
- **整個 repo 轉 public 或發到 PyPI。** 那才是根治（安裝字串縮成
  `uv tool install redmine-mcp`，`irm | iex` 也能用），但涉及「公司內部工具與指南要不要
  公開」的決策，不是純技術問題。與 2026-08-17 的設計文件結論一致，繼續擱置。

## 入口與交棒點

**Windows：**

```powershell
git clone --depth 1 https://github.com/Shinspire/MCP.git "$env:TEMP\shinspire-mcp"; powershell -ExecutionPolicy Bypass -File "$env:TEMP\shinspire-mcp\redmine-mcp\install.ps1"
```

**WSL／Linux：**

```bash
git clone --depth 1 https://github.com/Shinspire/MCP.git /tmp/shinspire-mcp && bash /tmp/shinspire-mcp/redmine-mcp/install.sh
```

`-ExecutionPolicy Bypass -File` 不可省：Windows 用戶端的預設執行政策是 `Restricted`，
直接 `& script.ps1` 會被擋，而錯誤訊息（「因為這個系統上已停用指令碼執行」）對非工程師
完全不可讀。這是本方案引入的新踩雷點，必須在入口就處理掉，不能留給使用者。

### shell 腳本的職責邊界

**只到「`redmine-mcp` 這個指令在 PATH 上可以執行」為止**，最後一行是
`redmine-mcp install`（把使用者給的旗標原樣傳過去）。shell 裡不做任何判斷分支以外的事，
不印安裝結果總結——那是 Python 的工作。

三個步驟：

| 步驟 | Windows | Linux／WSL |
| --- | --- | --- |
| 1. 檢查 git | 走到這裡代表已經有（否則拿不到腳本），只印版本 | 同左 |
| 2. 確保 uv | `winget install astral-sh.uv`，失敗退回 `irm https://astral.sh/uv/install.ps1 \| iex` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| 3. 裝套件 | `uv tool install --from "git+...#subdirectory=redmine-mcp" redmine-mcp` | 同左 |

腳本結束前刪掉暫存 clone。裝完後 `redmine-mcp` 已在 PATH，`--repair`／`--uninstall`／
升級全部直接跑 `redmine-mcp install --<模式>`，不需要再 clone。shell bootstrap 只在
「第一次安裝」與「套件壞到起不來」兩種情況出現。

失敗訊息要自己寫，不能讓底層工具的原生輸出直接出去。尤其 `git clone` 失敗（沒有 repo
存取權）必須講「請確認你的 GitHub 帳號有 Shinspire/MCP 的存取權」，而不是 git 原生的
`Authentication failed`。

## `redmine-mcp install` 的步驟模型

核心是**一份步驟表，四種模式只是對每一步套不同策略**，不是四條各自的流程。新增一步
只改一個地方，四種模式自動涵蓋。

### 步驟表

每一步是一個具備 `檢查()` 與 `修復()` 的物件，相依（`run`、`prompts`、`config_path`）
全部注入，沿用 `setup/` 現有的做法。

| # | 步驟 | 檢查 | 修復 |
| --- | --- | --- | --- |
| 1 | uv 可用 | `uv --version` | 印指引（shell 已處理過，走到這裡代表異常） |
| 2 | 套件已安裝且為最新 | `uv tool list` | `uv tool install` / `upgrade` |
| 3 | Claude Code CLI 可用 | `claude --version` | 問過後跑官方安裝端點 |
| 4 | 參數檔可用 | 讀得到、至少一個站台、欄位齊全 | 呼叫現有的 `run_setup()` |
| 5 | 已註冊到 Claude Code | `claude mcp get redmine` | `claude mcp add`（沿用 `external.py`） |
| 6 | server 起得來 | 現有的 `smoke_test()` | 無法自動修，印排錯指引 |

`wizard.py` 與 `config_ops.py` 不改動——步驟 4 直接呼叫現有的 `run_setup()`，是把既有
精靈當成一個步驟嵌進來，不是重寫它。

### 四種模式

| 模式 | 對每一步的行為 |
| --- | --- |
| 預設（重跑＝升級） | 檢查過就跳過；步驟 2 額外做 `upgrade` |
| `--repair` | 檢查更嚴：步驟 4 實際打一次 Redmine 驗金鑰、步驟 5 確認註冊指向的執行檔還在。只修不對勁的那幾步 |
| `--reinstall` | 步驟 2 強制 `uninstall` + `install`；其餘照常檢查 |
| `--uninstall` | 反向跑：拆註冊 → 問是否刪含金鑰的參數檔 → 移除套件 |

`--repair` 會需要有效的 Redmine 連線才跑得完，這是刻意的：repair 的用途就是回答
「為什麼壞了」，而「金鑰過期／網址被改」正是最常見的成因之一，不驗就等於少檢查一個
最可能的原因。

### 進度呈現

```
redmine-mcp 安裝

[1/6] uv 可用 ..................... ✓ 0.9.2
[2/6] 安裝 redmine-mcp ............ ✓ 0.8.0
[3/6] Claude Code CLI ............. ✓ 2.1.4
[4/6] 參數檔 ...................... → 需要設定，進入精靈
      ...（現有精靈的問答與 Rich 面板）
[5/6] 註冊到 Claude Code .......... ✓ scope: user
[6/6] 啟動測試 .................... ✓

完成。剩下兩件事要你自己做：
  1. 重開 Claude Code
  2. 輸入 /mcp 確認 redmine 顯示為 connected
```

輸出一律經現有的 `Prompts`，因此 cp950 終端的 `UnicodeEncodeError`
（`_tolerant_stdout()`）與 Rich markup 逸出這兩道已踩過的防線自動生效，測試對
「金鑰只顯示末四碼」的斷言也照樣涵蓋。

### `--uninstall` 的最後一步：誠實的限制

Python 沒辦法乾淨地移除自己所在的套件——Windows 上執行中的 `.exe` 被鎖住，
`uv tool uninstall` 會失敗。

設計：Python 完成拆註冊與刪參數檔（真正需要判斷的部分），然後嘗試
`uv tool uninstall redmine-mcp`；**失敗就退回印出那一行指令並說明原因**，不假裝
做完了。這與現有精靈「重開 Claude Code 要你自己做」的誠實原則一致。

## 錯誤處理

- **每一步失敗就停在那一步**，印出三件事：哪一步、為什麼、下一步做什麼。不繼續往下
  跑到一個更難懂的錯誤。
- **提權只問一次**：Windows 的 `winget install` 可能觸發 UAC、Linux 的套件安裝需要
  sudo。在第一次要動系統層之前問一次並說明會裝什麼，之後不再問。
- **exit code 有語意**：0 成功、1 使用者取消、2 前置缺失無法自動補、3 步驟失敗。
  讓它能被包進更外層的自動化。

## 安全紀律

沿用 2026-08-17 設計文件的同一條線，不放寬：

- API key 絕不進 argv、不進 log、不進任何例外訊息。因此 `install` **不提供**帶入金鑰的
  旗標，步驟 4 一律走精靈的不回顯輸入。
- 終端輸出一律只顯示末四碼。
- 安裝器不會把任何憑證寫進暫存 clone，暫存目錄用完即刪。

## 檔案配置

| 檔案 | 內容 |
| --- | --- |
| `redmine-mcp/install.ps1` | 新增，約 100 行，三步無分支邏輯 |
| `redmine-mcp/install.sh` | 新增，同上 |
| `src/redmine_mcp/setup/steps.py` | 新增：六個步驟物件，各自 `檢查()`／`修復()` |
| `src/redmine_mcp/setup/installer.py` | 新增：模式策略與流程編排、進度輸出 |
| `src/redmine_mcp/setup/external.py` | 擴充：`uv tool list/install/upgrade/uninstall`、`claude --version` 與官方安裝端點的呼叫 |
| `src/redmine_mcp/cli.py` | 新增 `install` 子命令與四個旗標 |
| `tests/test_setup_steps.py` | 新增 |
| `tests/test_setup_installer.py` | 新增 |
| `README.md`、`SETUP.md` | 安裝章節改寫成新的一鍵指令 |

## 測試策略

**Python 層**：六個步驟 × 四種模式，全部以假 `CommandRunner` 與假 `Prompts` 驅動，
沿用 `tests/test_setup_*.py` 現成的慣例。涵蓋分支：已裝／未裝、註冊過／沒註冊過、
參數檔缺欄位、使用者中途取消、`--uninstall` 最後一步失敗的退回路徑。

**shell 層**：不寫單元測試——這正是把它壓到三步無邏輯的理由。改以 **CI 端對端驗證**：
GitHub Actions 在 `windows-latest` 與 `ubuntu-latest` 上以 `GITHUB_TOKEN` clone 本
private repo，實際跑一次 `install.ps1`／`install.sh`，驗到「`redmine-mcp` 在 PATH 上且
`redmine-mcp install` 走到步驟 4 停下」為止（精靈需要互動，CI 不往下走）。

**順手補一個既有的洞**：`smoke_test()` 靠比對啟動 log 中的「啟動」二字判定成功
（`external.py` 的 `_STARTED_MARKER`），改掉 `__main__.py` 那行 log 會讓它靜默退化成
一律回報失敗，而現有測試全部用假 runner 餵固定字串，CI 不會示警。`install` 的步驟 6
也依賴這個判定，因此本次一併補上守住這條耦合的回歸測試。

## 不在範圍內

- `llm-wiki-mcp` 與 `release-doc-mcp` 的安裝器。
- 非互動模式（帶旗標一次填完設定）。理由同 2026-08-17：會把金鑰推向 argv。
- macOS。可能可用（pycurl 有 macOS wheel、`install.sh` 的 uv 安裝路徑相同），但不列入
  支援範圍，也不納入 CI 驗證。
- Alpine 等 musl 系統。
- 把 `install` 與既有 `setup` 合併成一個命令。`setup` 維持原樣，`install` 呼叫它。
