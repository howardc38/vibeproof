---
description: 把 N 件唔相干嘅工作,同時開 N 個 worktree、N 個 task、N 個 worker 去做。
---

# /wave

一次過開 N 刀,每刀一個 worktree、一個 task、一個 worker。**你係 orchestrator。**

呢個 command 唔係新機制 —— 佢係把已經手做緊嘅嘢寫低。實測喺 reference adopter:
八個 worktree、五個 task 同時開住,全部人手開,而唯一冇人記得嗰步就係下面第 5 步。

## 次序

```
1  分組       N 組,每組一個 file-level scope
2  worktree   git worktree add ../wt-<id> -b wave/<id>
3  task       v4 --repo ../wt-<id> task --id t-<id> --request '…' --scope '<嗰組嘅檔>'
4  engage     v4 --repo ../wt-<id> engage --task t-<id> --kind <kind> --actor splitter --text '…'
              ↑ 句子要提到一個喺嗰組 scope 入面嘅路徑,唔係就會被拒
5  開 worker  一個 message 一次過開 N 個                      ← 見下
6  驗收        v4 --repo ../wt-<id> accept
7  收          v4 --repo ../wt-<id> ship --task t-<id>
8  合          git merge wave/<id>  ×N,  git worktree remove ../wt-<id>
```

第 1 至 4 步係 `task-splitter` 嘅嘢，一組一次；每個 worker 先 derive，
再跟 `/run` 的 engagement、修改、檢查、request accounting 和 ship 流程，
喺各自嗰棵樹入面行。呢個檔只負責「同時」嗰部分。

## 第 5 步:一個 message 一次過開晒

```
每個 worker sub-agent 個 prompt 要帶:
    cd ../wt-<id> && export V4_TASK=t-<id>
```

用 host 支援嘅並行 dispatch；唔好等一個 worker 完成先開下一個。
Message 數目本身唔決定並行，亦要遵守 host 的 concurrency 上限。

**`export V4_TASK` 係呢個 command 存在嘅一半理由。** ledger 係成個 repo 共用嘅
(`ledger_path` 行 `git rev-parse --git-common-dir`,SPEC.md §1),所以幾棵樹嘅
task 全部喺同一張表度開住。write hook 冇咗 `V4_TASK` 就冇得知呢次寫入屬於邊個,
舊版本會靜默揀最新嗰個；現行 `open_task` 發現多於一個 open task 會回
`AMBIGUOUS`，hook 會拒絕呢次寫入。每個 worker 必須帶自己嘅 task ID。

`hooks/write_block.py` 自己寫住:「Two tasks open and no `V4_TASK`: the guard has
no way to know which one this is.」

## 一刀嘅來源:一個 request,或者幾條講緊同一件事嘅 finding

```
./bin/v4 status --task repo-review        ← 張單喺 ledger,唔喺任何文件度
```

`repo-review` 係 sweep 提嘅 finding 掛住嗰個常設 task。要修佢哋,一刀 = **幾條講緊
同一個事實嘅 finding**,唔係一條。

**點解唔係一條一刀 —— 量過:**

```
2026-08-19:  224 條 finding → 209 條真 → 26 個真實組
             原紀錄另報平均 7.4 條、最多 13 條；平均值的分母不清，不能由 209/26 推出，勿作目前成效數字
```

一條一刀,即係同一個事實喺 12 個地方修 209 次。呢個 repo 自己嘅 doctrine 就係反面:
**repairing a finding means repairing the fact everywhere it appears。**

## 分組跟語義,唔跟路徑 —— 呢個都量過

```
26 個真實組,平均跨 3.2 個檔
  只喺一個檔入面嘅:                    9 / 26
  一個檔嘅 finding 分散喺多過一組:      19 / 63 個檔
```

所以「按檔分組」兩邊都錯:會把 26 組入面 17 組拆爛,又會把 63 個檔入面 19 個撈埋。
按 lens 分一樣衰 —— 一個 root cause(「一條規則,N 個實作」47 條)本來就跨 lens。

**真實嘅組係:「同一個事實,錯咗喺幾個地方」。冇機械切法接得住,所以呢步係判斷。**

分完之後寫低佢:

```
./bin/v4 review group --name '<一句講嗰個事實>' --claim <id> --claim <id> … --why '…'
```

唔係為咗驗證(驗證已經有,見下),係為咗下次 sweep 見到同一批嘅時候,唔使由零再判一次。

## 分組錯咗會被捉到一半

一個 test 閂一組,而 `v4 check` 係**逐條 claim** 跑 `redgreen.verify` —— 每條都要求
**佢自己個 symbol 真係被執行過**。

實測:同一個 test 檔綁去四條 claim(`engagement.py::before_the_work`、
`review.py::defer`、`lifecycle.py::continues`、`doctor.py::run`),四次獨立 check,
四次 PASS,每次 2.0–2.2 秒。

```
❌ 驗唔到    「呢啲係同一個 root cause」（語義)
✅ 驗到      「一個 test 真係行過呢 N 個 symbol」（機械,已經行緊)
```

第二條唔等於第一條,但佢擋住咗最差嗰種錯分組。

## 每刀收工之前

```
./bin/v4 accept        宣告嘅 test command + 每個 checker + 每個 detector + 四個文件對 code
```

**唔好用本機 `python3 -m unittest` 當驗收。** 實測:1,285 個 test 本機全綠,push 上去
CI 紅 —— `spec-coverage` 捉到兩個新 command 冇入 SPEC。跑 test 唔等於跑閘。

## Scope 重疊與合併後驗證

每棵樹只 diff 自己,所以 A 棵樹嘅寫入喺 B 棵樹嘅 `scope` claim 度睇唔見。合併時仍可能有文字衝突或冇文字衝突的語義錯誤。合併後要重驗測試及
用 `v4 remerge`／status 檢查舊證據，不能把各自綠燈當成整合後已通過。

`task-splitter` 已經量過:**按檔案重疊嚟判要唔要拆,係被否證嘅 —— 89% 乾淨
auto-merge(n=36)。** 所以分組跟住 request 分,唔好為咗避開重疊而砌一個唔自然嘅切法。

## 幾多刀

Kernel 沒有在這份流程定工作數上限；host、資源和可安全合併的範圍仍有限。每刀都要有自己的 request。**一刀一個 request** —— `task-splitter`
嗰句唔係叫你唔好拆,實測兩輪:一個計劃拆咗六刀,一次 61 條 finding 嘅 sweep 拆咗
二十刀,每一刀有自己嘅 request、scope、claim。

## 收唔到就唔好淨低

一棵冇 ship 嘅 worktree 係一個永遠開住嘅 task,而一個開住嘅 task 會:

- 在其仍屬活躍工作且有 blocking claims 時令 `v4 sweep` 等待；只有 report-only 問題不一定算 busy
- 令 write hook 喺下一次冇 `V4_TASK` 嘅寫入度揀錯人

`v4 doctor` 嗰行 `open tasks` 會逐個名咁報返出嚟,連埋佢喺邊棵樹。收唔到就
`v4 abandon --task <id> --why '…'`,唔好當佢唔存在。
