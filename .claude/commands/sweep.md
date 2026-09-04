---
description: 後閘。到期就一個 lens 開一個 reviewer,全部並行,盲讀。
---

# /sweep

跑一次 lens sweep。**你係 orchestrator,唔係 reviewer —— 你一個 lens 都唔好自己讀。**

## 次序

```
1  v4 sweep                        到期未？
2  唔到期  →  停。呢度冇嘢好諗
3  到期    →  佢一個 lens 印一行 `v4 review lens --lens <名>`
4  一個 message 一次過開晒佢印嗰批 reviewer sub-agent   ← 見下
5  收齊    v4 sweep --done --findings <n>
```

**呢度唔寫死幾多個。** `.v4/lenses/` 加一個檔就多一個 reviewer,而呢份文件係四個地方
寫住同一個數字先至過期嘅 —— request-fidelity(2026-08-25 加)同 near-miss(2026-08-26 加)
兩個 lens 開咗一段時間都冇喺呢度出現過。數字由 `v4 sweep` 印,唔由呢度講。

## 第 2 步:唔到期就係唔到期

`v4 sweep` 唔應你嗰陣會講點解,而每個理由都係一個實測逼出嚟嘅守衛:

| 佢講 | 意思 |
|---|---|
| `the last sweep was Nd ago and the interval is 4d` | 未夠鐘。四日係 config 嘅 `lens_sweep` |
| `N task(s) still hold an unanswered claim` | 有人棵樹寫到一半。**呢個唔係叫你去催佢,係叫你唔好而家掃** |
| 讀唔到嘅 task | **讀唔到唔等於乾淨。** 當佢乾淨就係喺睇唔到嘅工作上面掃 |

⚠️ **而家逾期冇任何嘢擋住你,呢個要講清楚。** 曾經有一條 applies_to: always 嘅
sweep-current claim,逾期擋住 `v4 ship`;`a9ae5fb`(2026-08-24)量過佢跑咗 295 次
只 fail 過一次,連同佢個 detector 同 checker 一齊剷咗。今日淨返三樣,三樣都唔係閘:
`./bin/v4 --repo . sweep --if-due` 唔到期 exit 1、到期先印 brief;`v4 doctor` 嗰行
`sweep` 只講最後一次幾時,唔講夠鐘未;`.github/workflows/v4.yml` 星期一個 cron 讀
committed export 印 DUE 定 not due,而佢個註釋自己寫住唔係一個閘 —— 綠代表算得出,
唔代表有人睇過。

**即係「有冇開成一次 sweep」由呢個 command 有冇人叫決定。** 唔到期就停,係為咗唔好
掃一棵寫緊嘅樹;到期而冇人叫,冇任何嘢會擋住任何人。

## 第 4 步:一個 message 一次過開晒

```
每個 reviewer sub-agent 個 prompt 只准帶:
    lens 個 slug（唔係 display name）
    repo 路徑
```

**分開幾個 message 開,就係順序執行。** 實測記錄:
2026-08-18 嘅 sweep,11 個 lens agent 真並行,214 條 finding。
2026-08-18 同一個框架喺 adopter 度嗰次,ledger 寫住 `11 lens(es) claimed, 1 ran`。
個框架印埋兩個數,就係為咗捉呢個分別。

## 盲讀 —— 呢個 command 存在嘅另一半理由

```
⛔ 唔准傳落 sub-agent:
     worker 嘅 engagement 句
     task 嘅 rationale
     之前 sweep 嘅 finding 清單
     你自己對呢份 code 嘅睇法
```

理由係量過嘅:三個 reviewer 每輪出 3.6 條 finding,**每輪都係新嘅** —— 即係
reviewer 係一個抽樣器,唔係窮舉器。一個讀過解釋嘅抽樣器,會沿住嗰個解釋抽樣,
只會揾返有人已經諗過嘅嘢。

**kernel 攔唔到呢條。** 佢只見到 `v4` 嘅 command 同 ledger,唔知邊個 process 讀過
乜。`reviewer.md` 自己認:「佢係一條紀律,唔係一個邊界。」**而條紀律唯一守得住嘅位
就係呢一步 —— 開 sub-agent 嗰個唔傳落去。**

## 佢哋交返乜

Reviewer 出 claim,唔出 verdict,亦都唔修理:

```
./bin/v4 --repo . review add --lens <slug> --file <path> --symbol <包住嗰個 symbol> \
                             --note '<一句,錯咗乜>'
```

`--lens` 唔可以省(claim id 由佢派生,兩條撞埋第二條會變成第一條嘅修訂);
`--symbol` 要係一個 stack frame 叫得出名嘅嘢(module-level 常數會即場拒 —— 冇 test
執行得到,即係嗰條 claim 永世閂唔到,唯一出口係簽名)。

## 之後

Finding 落喺 `repo-review` 呢個常設 task,唔係落喺你手上。閂佢係另一件事:
一個 red-green test(parent 紅、HEAD 綠、而且真係執行過嗰個 symbol),或者一個人簽名。
要並行去修,用 `/wave`。

`v4 risk waiting` 會講邊條係「重跑就得」、邊條係「只有簽」。
