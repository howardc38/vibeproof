<!-- Language switcher. Keep this line identical in all three files. -->
[English](README.md) · [简体中文](README.zh-CN.md) · **繁體中文**

# vibeproof

### 你的 coding agent 說「做好了」。這套東西要它拿出證據。

vibeproof 是一組放在你 git repo 裡、伴著 coding agent 運作的小程式。每次 agent 完成一件工作，它可能出錯的每一點都會變成一條**問題**。由一個程式用 exit code 回答那條問題。答案寫進一本沒人能改的帳。**有問題未答就不能出貨**：agent 要麼修好 code，要麼由一個人簽名承擔那個風險。

它不寫 code，不取代你的 CI 或 linter，也不判斷設計對不對。它只答一件更窄的事：**「你說你做了，證據在哪？」**

---

## 這是不是你需要的？

先看左欄。如果有兩行以上是你真的中過招的，就值得繼續讀。

| 你的痛點 | vibeproof 處理得到嗎？ | 怎樣處理 |
|---|---|---|
| agent 說「加了測試，全綠」，但那條測試**從來沒有呼叫過那個函式**，只是檢查源碼裡有沒有某段文字 | **可以。** | 由 Python 直譯器自己報告哪些函式跑過（`sys.settrace`）。一條沒進入過函式的測試不能關閉 review finding。`test-shape` 更會在測試還沒跑之前就標出「這條測試在讀源碼」 |
| agent「重構」了，**順手刪掉兩條測試**，套件因為刪了而變綠 | **可以。** | `test-weakened` 拿 base commit 時的 test function 同現在的逐個對 |
| 套件全綠，但**根本沒執行過 agent 改的那個檔** | **可以。** | `test` checker 在 tracer 下跑你的測試命令，套件綠但沒碰過任何改動檔就判 FAIL |
| 「加了錯誤處理」，而那個 `except` **把付款、webhook、授權檢查的失敗吞掉了** | **可以**，Python、Go、TS/JS。 | `fail-closed` 找出包住對外呼叫或授權判斷的 try/except，而控制流可以在沒有例外傳出的情況下離開 |
| 一次對外寫入（扣款、發文、發佈）**沒有 read-back**，或者**重試時會寫兩次** | **可以**，前提是你先告訴它你的 codebase 裡什麼算對外呼叫。 | `external-write` 讀一份你自己維護的小詞表（`.v4/facts.<repo>.json`），對每個呼叫點問這兩條問題 |
| agent **改了根本沒叫它碰的檔** | **可以**，在寫入那一刻。 | 一個 hook 對每次 Write 和 Edit 查 task 宣告的 scope，外面的直接拒絕 |
| 憑證**被 commit 了** | **可以。** | `secret` 掃這個 task 改過的檔，印出來之前先遮掉 |
| 某個 scanner 印了 `PASS`，其實**一個檔都沒打開過** | **可以。** | exit code `4` 的意思是「這裡我讀不到任何東西」。它永遠不是 pass，也永遠不會結束一個 task |
| agent **沒做完就停了**，或者放棄了也不講 | **可以**，擋一次。 | stop hook 列出未答的問題，拒絕第一次停手。修好、簽名、或明講「我留下這個 FAIL」，唯一不接受的結局是靜靜走掉 |
| 你想**日後**查得到誰對哪個版本的 code 簽了什麼 | **可以。** | 每次檢查和每個簽名都是一本 append-only、hash 鏈住的 SQLite 帳裡的一行，簽名同時是一個必須 commit 的檔 |
| 「這個設計對不對？」「它真的做了我要的東西嗎？」 | **不能。** | 這裡沒有程式讀得懂你的意圖。reviewer lens 可以提出這條問題，答的是人 |
| 你想少信一點 model | **不能**，這也不是目標。 | 它令你在機械的部分不需要信任。判斷仍然是你的 |
| 你主要寫 Rust | **幾乎沒有。** | 今天只有兩個檢查讀 Rust。見[語言支援](#它讀得懂你的語言多少) |
| 你想要一道 agent 繞不過的牆 | **不是。** | 這是有紀錄的摩擦，不是邊界。有 shell 的 agent 可以改一個普通 SQLite 檔或刪掉 trigger。你得到的是：篡改會在 audit 裡顯現，每條逃生路都會留下一行帶名字的紀錄 |

---

## 一個 task 長什麼樣

以下是 2026-09-02 對一個三檔沙盒 repo 的真實執行。你可以重現：code 在下面，命令在 Quickstart。

agent 寫了這段，說是「加了錯誤處理」：

```python
import requests

def charge(card):
    try:
        requests.post("https://api.example/charge", json={"card": card})
    except Exception:
        pass
    return True
```

`v4 derive` 對這個 task 提出七條問題。其中五條拒絕跑 checker，直到 agent 先寫一句「這條規則對這段 code 意味什麼」。然後 `v4 check`：

```
FAIL fail-closed      src/pay.py:7  in charge  [swallow]
     except Exception guards outbound call `requests.post` (line 6), and control
     can leave this try/except with no exception having propagated

FAIL external-write   src/pay.py:6  in charge  [readback/unobserved-write]
     the outbound write `requests.post` at line 6 discards what it returned, and
     `charge` calls nothing from the outbound-read table.
```

agent 試圖停手。stop hook 回答：

```
3 claim(s) on t-e2e are still open:
  OPEN    fad124898937cac4  external-write
  OPEN    3ce1741909dec365  fail-closed
Three ways to end this properly: answer them, sign for one, or say it failed.
```

修好之後（加 `raise_for_status()`，並把 receipt 讀回來）兩條都 `PASS`。然後 `test` 那條因為另一個原因轉紅：

```
FAIL test   the suite passed and executed none of the 1 changed file(s):
              src/pay.py
            A green suite that never reached the change proves the suite works.
            It says nothing about this change.
```

整個想法就在這一屏：**「綠」和「真的檢查過」是兩件事，而第二件一直都是缺的那件。**

---

## Quickstart

你需要一個 git repo、Python 3.12、你自己的測試命令，以及（要用 hook 的話）Claude Code。不需要從 PyPI 裝任何東西：這個框架零第三方依賴。

**每個 repo 做一次。**

```sh
# 1. 取得框架。它留在你的 repo 外面；你的 repo 只會得到一個 launcher。
git clone https://github.com/howardc38/vibeproof.git ~/vibeproof

# 2. 在你的 repo 建立 .v4/。它寫好 config，留下四個它拒絕替你猜的問題，
#    第一個就是你的測試命令。
~/vibeproof/bin/v4 --repo /path/to/your/repo init

# 3. 至少答第一個。
#    在 /path/to/your/repo/.v4/config.json 設定
#      "test_command": "python3 -m pytest -q"      # 或任何能跑你套件的命令

# 4. 安裝 checker、detector、hook 和 lens。每個 checker 入場前都要先過
#    自己的 fixture，所以要幾分鐘。
~/vibeproof/bin/v4 --repo /path/to/your/repo install

# 5. 把 hook 接進 Claude Code，然後檢查接線。
cd /path/to/your/repo
cp .claude/settings.template.json .claude/settings.json
./bin/v4 doctor
```

`install` 同時會起草 `.v4/facts.<repo>.json.draft`：它對「你的 code 裡哪些呼叫會寫到外面世界」的最佳猜測。讀一次，刪掉猜錯的，改名為 `.v4/facts.<repo>.json`，再跑 `./bin/v4 facts validate`。在你做這件事之前，`external-write` 用一份通用詞表運作，而 `ship` 會因為 installer 寫下的每一條佔位「absent」而擋住。[docs/FACTS.md](docs/FACTS.md) 解釋這個檔。

**每件工作。** 在 repo 裡開 session，Claude Code 才會載入 hook。然後：

```sh
./bin/v4 task --id t-1 --request '<請求原文，用要求者自己的字>' --scope 'src/**'
./bin/v4 derive --task t-1                       # 這個 task 引出哪些問題
./bin/v4 engage --claim <id> --text '<這條規則對這段 code 意味什麼>'
#   ... 寫 code ...
./bin/v4 check --task t-1                        # 跑 checker，讀 exit code
./bin/v4 ship --task t-1                         # SHIP 或 HELD，連理由
git add -A && git commit                         # 簽名和帳本匯出跟 code 一起走
```

[docs/USING.md](docs/USING.md) 逐步講每一步，以及每一步要下的判斷。

---

## 它怎樣運作

### 詞彙

| 詞 | 是什麼 |
|---|---|
| **task** | 一件工作：要求者的原話、它可以碰的檔、它起步的 commit |
| **scope** | 這個 task 可以改的檔的清單。hook 在每次寫入之前查它 |
| **claim** | 關於 code 的一條未答問題，例如「這次對外寫入，跑兩次會怎樣？」。它不是判詞 |
| **kind** | claim 的類型。21 種。類型決定由哪個程式回答，以及未答會不會擋出貨 |
| **detector** | 讀 diff、決定哪裡值得問的程式。它提出 claim，從不判。20 個 |
| **checker** | 用 exit code 回答一條 claim 的程式。它判，從不提出。21 個 |
| **engagement** | 對 10 種 kind，agent 寫第一行 code 之前必須先寫一句：這條規則對即將寫的 code 意味什麼。由七條機械判準判這一句，沒有人判 |
| **signature** | 程式證明不到的東西可以由人簽名承擔，附理由。簽名會隨它看過的 bytes 過期，同 pass 一樣 |
| **ship** | 最後一條問題：每條 claim 都答了或簽了嗎，帳本的鏈完整嗎，每條宣告的 absence 都確認了嗎 |
| **lens** | reviewer subagent 讀 diff 時用的一組問題，看不到 worker 的解釋。13 份。它提出 finding，從不出判詞 |
| **ledger** | `.git/v4/` 裡一個 append-only 的 SQLite 資料庫。每次檢查和簽名是一行。逐行 hash 鏈住；匯出檔跟 code 一起 commit |

**兩個角色，永不合併。** detector 提出、從不判；checker 判、從不提出。你或你的 agent 站在中間：答那條問題，或為它簽名，兩樣都會留下一行。

### checker 的 exit code 是什麼意思

| Exit | 名字 | 意思 |
|---:|---|---|
| `0` | PASS | 看過，沒發現。claim 算答了，直到它任何一個輸入改變 |
| `1` | FAIL | 看過，有發現。claim 保持 open |
| `4` | UNSUPPORTED | 這裡讀不到任何東西。**不是 pass，也不是結局** |
| `5` | ERROR | checker 自己崩了 |
| `6` | CHECKER_TAMPERED | disk 上的 checker 不是註冊的那一份。沒有執行 |
| `7` | SUBJECT_MOVED | checker 跑的期間 code 變了 |
| `8` | TIMEOUT | 沒跑完 |

### 哪些問題會擋出貨

八種 kind 未答或未簽就擋住 ship：`test`、`scope`、`secret`、`fail-closed`、`external-write`、`review-finding`、`runtime-proof`、`surface-proof`。其餘十三種只報告，直到同一種堆積（10 條未答）、放久（14 日）或反覆失敗（5 次），才一樣擋住。

### 一個答案有效多久

每個答案釘住六個輸入：它判過的檔的 digest、config、checker 自己的 bytes、detector 的 bytes、facts 詞表，以及（對全 repo 問題）整棵樹。**任何一個移動，答案就變 STALE**，要重問。簽名用同一把 key，所以簽名永遠不會比 pass 便宜。

### 三個 hook

| Hook | 觸發於 | 拒絕什麼 |
|---|---|---|
| `write_block` | Write、Edit、MultiEdit、NotebookEdit | task scope 以外的路徑；保護路徑；還欠 engagement 句子時的任何寫入 |
| `bash_guard` | Bash | 會寫入保護路徑的 shell 命令，或提到保護路徑而無法證明只是讀的命令 |
| `stop_gate` | Stop | 有 claim 未答或 task 未 ship 時結束回合，只擋一次。第二次放行，FAIL 留在帳本裡 |

三個都是 **fail open**：hook 找不到 kernel 或帳本時放行，並在 stderr 說明。一個什麼都擋的 hook，是一個會被人關掉的 hook。

### 保護路徑

四個路徑決定其他一切怎樣被判，所以改它們要一個簽名，不只是 widen scope：`.v4/**`（config、registry、facts）、`checkers/**`、`detectors/**`、`.github/**`。

### 判官先被判

checker 要過完整套 fixture 才能進 registry：`red/` 必須 exit 1，`green/` 必須 exit 0，`bypass/`（同一缺陷改寫成規避的樣子）必須 exit 1 且不能是 red 案例的副本，`known_miss/` 明文寫下一個盲點。今天 21 個 checker 共 525 個案例。過不了的 checker 永遠判不了任何東西。

---

## 21 條問題

每一行是 registry 裡的 `question_template` 原文。最後一欄是這種 kind 未答會不會擋 ship。

| Kind | 它回答的問題 | 擋 ship |
|---|---|:---:|
| `test` | Does the repo's declared test command pass?（以及這次執行有沒有碰到改動的檔） | ✓ |
| `scope` | Does the diff stay inside the declared scope? | ✓ |
| `secret` | Do the files this task changed contain a committed credential? | ✓ |
| `fail-closed` | Can control leave the try/except at {file}::{symbol} with no exception having propagated? | ✓ |
| `external-write` | After the outbound write at {file}::{symbol}, does anything establish it landed, and does replaying it apply the effect twice? | ✓ |
| `review-finding` | Is the finding at {file}::{symbol} closed by a test that fails without the fix and actually runs the code? | ✓ |
| `runtime-proof` | Did triggering this leave a row in the table that owns the data? | ✓ |
| `surface-proof` | Does the surface suite this repo already maintains still pass? | ✓ |
| `test-shape` | Does {file} prove behaviour, or only that some text is present? | |
| `test-weakened` | Does {file} still hold at least the test functions it held at the base? | |
| `test-expectation` | Did {file}::{symbol} change what it expects while the code it judges did not? | |
| `test-token-shape` | Is there a literal in tests/ that looks like a real credential without saying it is fake? | |
| `signature-change` | Did every caller follow the parameter this change made required? | |
| `dangling-ref` | Does {file} import {symbol} from a module that defines it? | |
| `lint` | Does this task add a structural violation that was not in the base commit? | |
| `layer-boundary` | Does any import cross a layer the declaration does not allow? | |
| `design-pins` | Do the design document's pinned claims about the code still hold? | |
| `control-plane-budget` | Is the control plane still within the ceiling it declared? | |
| `dead-wiring` | Is anything declared here with something on one end and nothing on the other? | |
| `registry-consistency` | Do the registries still describe the checkers, detectors and fixtures on disk? | |
| `spec-coverage` | Does every command, path, kind and mechanism the spec names actually exist? | |

最後四種是關於這個框架自己的，裝到別的 repo 時會被扣起。`layer-boundary` 要等你的 repo 宣告了 `.v4/layers.json` 才會裝。`runtime-proof` 和 `surface-proof` 要 config 裡有命令才有東西可跑。

---

## 它讀得懂你的語言多少

十個 checker 不分語言讀每個檔（`scope`、`secret`、`test`、`review-finding`、`external-write`，以及五個自我一致性檢查）。其餘的在 `.v4/checkers.json` 宣告自己讀哪些副檔名。在你的 repo 裡沒東西可讀的 checker 不會被安裝，而不是裝了然後對從未打開的檔印 `PASS`。

| 語言 | 讀它的 checker | 怎樣讀 | 「測試真的執行過那段 code 嗎？」 |
|---|---:|---|---|
| **Python** | 10 | 標準庫 `ast` | **可以。** 經 `PYTHONPATH` 上的 `sitecustomize` 用 `sys.settrace`；pytest、unittest、任何 Python runner 都行。`test` 和 `review-finding` 都用它 |
| **Go** | 7 | Go 自己的 `go/ast`，透過一個用你的 Go toolchain 編譯的小程式 | **checker 層仍未接上。** kernel 讀得懂 `go test -coverprofile`，但 `review-finding` checker 目前只接受 Python 的關閉測試，其他一律回 `4` |
| **TypeScript / JS** | 8 | 正規表達式遮罩的掃描器，不是 parser；有 `bypass/` fixture 盯著它 | 同 Go：kernel 讀得懂 `NODE_V8_COVERAGE`，但沒有 checker 走到那裡 |
| **Rust** | 2 | 掃描器，沒有 parser | **不能。** |

數字是 `reads` 明文寫出該語言的 checker 數，截至 2026-09-02。

---

## 它不會做的事

以下每一項都對著 code 核過，不是政策宣示。

- **它是摩擦，不是邊界。** hook 是 Claude Code 的設定，它們 fail open，有 shell 的 agent 可以改那個 SQLite 檔或刪掉 trigger。你得到的是 audit 會顯示出來，以及每條逃生路都留下一行帶名字的紀錄。
- **shell guard 只看命令列。** `python3 -c "open(...)"` 或一個打開保護檔的 script 會過。真正作準的是 ship 時的 `scope` checker。
- **簽名可以由 agent 落。** `--no-tty-check` 是合法出口；它寫的檔會註明 `signed_by: agent`。真正把簽名釘住的是：檔案要先 commit，`ship` 才接受。
- **engagement 句子是機械判的。** 四十字、提到 subject、不覆述規則、跨 task 不重複、不貼憑證。知道規則的人可以刻意滿足。目的是令那一刻發生，不是證明它發生過。
- **分析是單檔、詞表驟動的。** 另一個檔裡的 read-back 對 `external-write` 是看不見的。預設答案是不安全那邊的 exception allowlist 對 `fail-closed` 是看不見的。兩個盲點都以可執行的 `known_miss/` fixture 保存。
- **review finding 也可以用文字改動關閉**，不只靠測試：一段 24 字以上的標記在 parent commit 有、HEAD 沒有。它證明一段字串移動了，僅此而已。
- **`task` 表不在 hash 鏈內。** attempt 和 event 在；改寫 request 原文不會被 `v4 audit` 抓到。
- **沒有人執行 reviewer 盲讀。** kernel 不知道哪個 process 讀過什麼。
- **它不判斷請求有沒有被滿足。** `v4 cover` 要你逐句、逐字交代請求，它不下判斷。

---

## 數字

以下全部由 script 在 2026-09-02 從這棵樹算出，不是記憶。

| | | | |
|---:|---|---:|---|
| **21** | 條問題，每條一個 checker | **20** | 個 detector（11 個條件式、9 個無條件） |
| **8** | 種會擋 ship 的 kind | **13** | 份 reviewer lens，303 條 check |
| **96** | 條生成到 `CLAUDE.md` 的常設規則 | **32** | 個 `v4` 命令 |
| **525** | 個 fixture 案例（196 red · 212 green · 100 bypass · 6 known miss · 11 self-trigger） | **1,027** | 個各語言 fixture 檔（747 Python · 166 Go · 89 TS/JS · 25 Rust） |
| **1,717** | 條框架自己的測試 | **0** | 個第三方依賴 |

這個框架從 2026-08-06 起就是自己的第一個使用者。[docs/EVIDENCE.md](docs/EVIDENCE.md) 背後的量度和 [docs/DOGFOOD_LOG.md](docs/DOGFOOD_LOG.md) 的例子來自那本開發帳，它住在 `.git/v4/`，每次 ship 匯出到 `.v4/ledger_export.jsonl`。沒有帶著匯出檔的 repo 快照重現不了那些數字；它們是一份紀錄，不是對你正在讀的這棵樹的宣稱。

---

## 接著讀什麼

| | |
|---|---|
| [docs/USING.md](docs/USING.md) | 一個 task 的七步，以及每步要下的判斷 |
| [docs/FACTS.md](docs/FACTS.md) | `external-write` 讀的詞表檔，以及怎樣寫你自己的 |
| [docs/SPEC.md](docs/SPEC.md) | 契約：每個機制、命令和約束，釘在 code 上 |
| [docs/RATIONALE.md](docs/RATIONALE.md) | 為何這樣建，包括試過又撤回的 |
| [docs/EVIDENCE.md](docs/EVIDENCE.md) | 量過什麼、何時、有多穩 |
| [CLAUDE.md](CLAUDE.md) | 常設規則，由這個 repo 註冊了什麼生成 |

MIT 授權。
