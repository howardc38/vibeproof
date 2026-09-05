# 從短 demo 到自己的第一個 task

先跑 [獨立示範](../examples/first-proof/README.md)，再把完整 framework 用在一個可以還原的 git 專案。最容易開始的環境是 Claude Code、Python 3.12+，以及已經能執行的測試。

## 你決定，agent 執行

你提供想要的結果、允許修改的檔案、真正的測試命令，以及是否接受未解決風險的決定。agent 可以處理安裝、流程命令、讀取檢查結果與修正程式。你不需要每次親自輸入所有命令。

## 先完成安裝

保留安裝前可以還原的 commit。若已下載 vibeproof，使用現有 checkout，不要重複 clone 到同一個目錄。

```sh
git clone https://github.com/howardc38/vibeproof.git ~/vibeproof
~/vibeproof/bin/v4 --repo "/absolute/path/to/project" init
```

把路徑換成你的專案。然後在專案的 `.v4/config.json` 設定真正的測試命令，例如：

```json
"test_command": "python3 -m unittest discover -s tests -v"
```

這只是一個 JSON 欄位，不是整份 config。保留其他設定。先確認測試命令在 framework 外可以執行；完全安靜或無法辨識測試摘要的命令，可能被拒絕。

```sh
~/vibeproof/bin/v4 --repo "/absolute/path/to/project" install
```

完整安裝會複製 checker、detector、fixtures、hooks 及提示檔，並驗證 fixtures，通常需要數分鐘。它會增加很多檔案；一個本地小例子有超過 1,500 個 fixture 檔案。核心 kernel 仍留在 vibeproof checkout，不能安裝後就把它刪掉。

## 核對 facts，接上 hooks

installer 會產生 `.v4/facts.<repo>.json.draft`。逐項對照程式，修正猜錯的呼叫與類型。每個 `AUTO:` 不存在宣告，都需要確認後以自己的話說明；不要只是把 `AUTO:` 刪掉。

檢查完，把檔案最後的 `.draft` 去掉，再執行：

```sh
cd "/absolute/path/to/project"
./bin/v4 facts validate
```

如果專案有 UI 或外部寫入，就配置真的能執行的 surface/runtime 證明；沒有的能力才明確記錄不適用。不要用固定輸出或空命令製造通過結果。[完整欄位與格式](GETTING_STARTED.md)、[facts 格式](FACTS.md)。

- 沒有 `.claude/settings.json`：可由 `.claude/settings.template.json` 複製建立。
- 已有 settings：只合併 template 的 hooks，保留原本 hooks、權限與其他設定，不能整份覆蓋。
- 在目標 repo 開一個新的 Claude Code session，再執行 `./bin/v4 doctor`。

`doctor` 檢查接線；exit 0 不代表每個 hook 已實際觸發。最後的 task 報告會顯示觀察到的 hook 活動。

## 貼給 agent 的第一個任務

換好要求與路徑後，可以貼以下指示：

```text
請用這個 repo 已安裝的 vibeproof 流程完成一個小任務。

要求：<我要的具體行為>
允許修改：<這次的原始碼與測試路徑>

先執行 ./bin/v4 doctor，讀清楚設定問題。用我的要求原文開 task，
產生檢查項目並讀相關規則。修改前完成需要的 engagement 說明，
再實作功能及真正的回歸測試，執行 checks、交代需求覆蓋，
按照已安裝 /run 的角色分工完成 ship。

不能執行的檢查要說明缺什麼。任何風險接受都先把理由交給我決定，
不要自行簽走。最後列出通過、仍在報告、unsupported 的項目，
hook 有沒有觸發，以及實際的 SHIP／HELD 結果。
```

這是一段方便使用的指示，不是新的安全邊界；kernel 本身存在 agent 簽署路徑。

## 怎樣知道可以繼續？

| 結果 | 怎樣處理 |
|---|---|
| FAIL | 看具體原因，修正後重跑 |
| STALE | 相關輸入改過了，按提示重新 derive／check |
| UNSUPPORTED | 補環境、命令或證據，不能說成已通過 |
| Report-only | 未必立即阻止 ship，但仍要檢視及交代 |
| HELD | 修正列出的原因；重複叫 ship 不會代替修正 |
| SHIP | 設定的判定已通過；仍要看留下的報告與 hook 提示 |

SHIP 不會替你部署，也不保證需求、安全性或所有分支正確。Stop hook 只攔第一次，下一次可以放行。

完整操作細節見 [USING.md](USING.md)（含更多角色與判斷），[技術限制](REFERENCE.md) 亦應保留在採用決定中。

## 第一個 task 後

告訴我們：哪一步最麻煩？有抓到你確認值得修的問題嗎？哪些是誤報？下一個任務會不會繼續用？[回饋入口](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml)。只需自願分享去敏紀錄，不需要私人程式或憑證。
