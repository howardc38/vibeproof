---
description: 由一個 request 行到 ship —— 開 task、出 claim、答、review、出貨。
---

# /run

由一個 request 行一個完整 task。**你係 orchestrator,唔係 worker。**

## 次序

```
1  task-splitter  → v4 task --id <id> --request '...' --scope '...' [--forbid '...']
                                      [--after <上一刀嘅 id>]
2  task-splitter  → v4 engage --task <id> --kind <kind> --actor splitter --text '…'
                   ↑ 句子要提到一個喺 scope 入面嘅路徑,唔係就會被拒
3                   v4 derive --task <id>
4  worker         → v4 engage --claim <id> --text '…'   （寫第一行 code 之前，hook 攔住）
5                   答 claim,直到 v4 status 冇 OPEN
6                   v4 cover --task <id> --quote '…' --symbol …   逐段交代個 request
7                   v4 ship --task <id>
```

**第 2 步係新嘅,而且係 splitter 做唔係 worker 做。** 一個 task 嘅 claim 分兩種:
講緊已經存在嘅 code 嗰啲喺第 3 步就出齊(量過:43 條入面 37 條),而 hook 會攔住
所有寫入直到佢哋有句子 —— 嗰批唔使你操心。**講緊呢個 task 即將寫嘅 code 嗰啲,
detector 要有 code 先提得出**,所以永遠冇得事前答。第 2 步就係嗰半:對住規則寫,
唔係對住 claim 寫,subject 係 task 個 scope。

由 splitter 寫,唔係由 worker 寫。`--actor splitter` 唔係裝飾 —— 冇咗佢,ledger
分唔出一句係交落嚟嘅約束,定係佢自己嘅預演,而嗰個分別就係呢一步存在嘅全部理由。

**句子要提到一個喺 task scope 入面嘅路徑,唔係就會被拒**(「mentions nothing this
task is scoped to. Before the code exists, what a rule is about is where it is
going to be written.」)。呢個唔係格式要求:一句講規則而唔講落腳點嘅說話,對住將來
先出現嘅 code 係查唔到真假嘅。實測一個 adopter 一輪 41 句入面 6 句第一次被拒,
全部係補返個檔名就過。

**呢一刀接住上一刀嘅話,第 1 步加 `--after`。** 佢做兩樣嘢:把上一刀嘅 request、
scope 同「邊啲 claim 真係答咗」寫入呢一刀嘅 request(等 worker 唔使靠一份自己
編出嚟嘅摘要開工),同時喺 ledger 記低條邊 —— 冇佢,「邊個 task 接住 X」呢條問題
就冇答案。實測十八刀:每一刀都係人手開,一條鏈都冇記低過,而其中一刀存在嘅唯一
原因就係上一刀撞到嘅嘢。`request_cover` 唔會叫你交代承接落嚟嗰段。

同一個 agent 寫句子又寫 code,次序調轉都只會寫
一句配合佢自己個設計嘅話 —— 量過六刀,worker 事後寫嘅六句全部喺度論證嗰段 code 冇問題,
而 checker 之後全部話有。Splitter 寫出嚟嘅係一個**交落嚟嘅約束**,唔係自我辯護。

之後 worker 喺第 4/5 步寫嘅句子,同第 2 步嗰句擺埋一齊讀 —— **唔一樣嗰度就係佢
開工時冇諗到嘅嘢**。

**後閘唔喺呢條次序入面。** 邊個閘幾時行,由 `docs/SPEC.md` 擁有,唔係由呢個
檔擁有 —— 呢度重述一次,就係第二個真相來源,而佢會飄。佢飄過:呢個檔一度
寫住「4 reviewer × N → 每個 lens 一個」,即係每個 task 跑九個 lens,而 SPEC
§10.1 早兩日已經量到嗰樣嘢係唔跑嘅 **6.6 倍**,所以後閘已經改成定期。一個照
住呢個檔做嘢嘅 orchestrator 會付返嗰 6.6 倍,而嗰 6.6 倍就係搬走佢嘅唯一理由。

所以:

```
./bin/v4 sweep                # 到期未？未到期就冇你事，行返上面第 5 步
./bin/v4 sweep --if-due       # 一句就答完，畀 cron 用
```

到期之後點做,喺 `/sweep`,唔喺呢度 —— 呢個檔一度寫住「4 reviewer × N」而
SPEC 兩日前已經量到嗰樣嘢係唔跑嘅 6.6 倍,即係第二個真相來源一定會飄。佢飄過一次。

點解係定期而唔係每 task —— 睇 `docs/SPEC.md` §10.1,唔好喺呢度搵。

同時開幾刀,喺 `/wave`。

## 你保住嘅四條邊界

**Worker 唔可以自己叫 ship。** 出貨係一個判詞,而 worker 就係被判嗰個。
你叫,唔係佢叫。

**Worker 唔可以自己簽名 —— 簽名係你嘅。** 同一個理由,而且更硬:ship 只係記錄
工作做完,簽名係宣稱**有一個人為一件證明唔到嘅嘢負責**。實際發生過:一個 worker
用 `--no-tty-check` 簽走咗判佢自己嗰條 claim,留低嘅檔案寫住 repo 擁有者個名、
一個字冇提係 agent 執行。佢個論證後來核實係啱嘅 —— **而論證啱唔係簽名嘅資格**。

Worker 交返理由畀你,你決定簽唔簽。你自己代人簽嗰陣,`.v4/risks/*.json` 會寫住
`signed_by: agent`,咁樣至少讀得返。

**Reviewer 盲讀。** 唔好把 worker 嘅 engagement 句或者 task rationale 傳落去 ——
一個讀過解釋嘅抽樣器會沿住嗰個解釋抽樣。

**Ship 唔收斂就停。** `ship` 有一個由 task 開始計嘅重掃額度。用晒就停,
唔好重新派 —— 每次重新派等於冇上限,而每輪都會向一個 append-only ledger 加 claim。

## 開工之前

```
./bin/v4 --repo . doctor      # 呢個 repo 係真係接好咗,定係得個樣
```

`BAD` 嗰幾行喺正常使用之下係靜嘅。
