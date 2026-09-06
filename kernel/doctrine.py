"""Layer ① — the rules that are true before any claim exists.  SPEC.md §14.

Every other layer here is triggered. A checker fires because a detector raised a
claim; an engagement sentence is asked for because a claim of that kind exists.
That structure has a hole in it, and the hole is the largest class of failure
this project is aimed at:

    a rule the model knows, and does not apply at that moment

`Do not accept a stopgap that only works for now` has no detector. There is no
AST shape for it, no exit code, no claim. So under the four-layer structure it
could never be an engagement rule -- not because nobody wrote it down, but
because there is nothing for it to attach to. It belongs in the layer that is
present before anything triggers, and that layer was empty.

Three properties, each of which the predecessor got wrong in a measurable way:

  generated, not written    A repo's file is produced from this module plus the
                            repo's own registry, so adding a checker updates the
                            doctrine. The predecessor hand-maintained overlays;
                            all seven of one document's overlays pointed at a
                            base section that had been deleted.

  short, and the length is  The predecessor's standing corpus was ~3,400 lines
  the whole discipline      and it made you prove you had read it: 13,570 reads
                            across 621 files, 349 MB, 14.2x re-reads of the same
                            bytes. Removing the proof was right. Removing the
                            corpus as well was not -- that left nothing standing
                            at all. The ceiling here is what remains after
                            subtracting everything a checker fires on and
                            everything a reviewer lens already says.

  no gate                   Nothing verifies these were read, and nothing should.
                            "Prove you read it" is the mechanism that produced
                            349 MB and changed nothing.

A rule earns a place here only if it fails all three of these:
  - a checker could fire on it            -> it belongs in layer ②
  - a reviewer could judge it from a diff -> layer ③
  - it attaches to a claim kind           -> layer ④
"""

from . import layout
from pathlib import Path

#: The standing set, grouped.  Every line here came out of the predecessor's
#: 33 guideline documents read line by line: 1,934 obligations, of which 1,265
#: belong to a model this project does not have (steps, cycles, contracts,
#: read evidence), 242 looked mechanisable, 171 are a reviewer's judgement and
#: 77 attach to a claim.  What is left -- deduplicated across documents, since
#: one rule appearing in five files means the predecessor said it five times
#: rather than that it matters five times -- is 254 unique rules, and this is
#: the subset that fails all three of the tests above.
#:
#: The count is not a quota. It is the remainder after subtracting everything a
#: checker fires on and everything a lens already says -- and it is not written
#: here, because it was: this said "79 lines" beside a list that holds 90 rules
#: in 11 groups, and `render` puts every one of them into each adopter's
#: CLAUDE.md while `drift` compares the generated block only against itself. A
#: number in a comment about the list underneath it is the one number nothing
#: settles. `v4 doctrine --check` counts the list. The predecessor's
#: standing corpus was ~3,400 lines and it made you prove you had read them:
#: 13,570 reads across 621 files, 349 MB, the same bytes read 14.2 times.
#: Removing the proof was right; removing the corpus with it left nothing
#: standing at all, and that is the hole this fills.
DOCTRINE = [
    ('驗證', [
        '對著真正的機制驗證。用 stub、mock、重跑一次測試套件、health check 或檢視簽名去頂替，都是繞道。',
        '替代品只是「比較容易」而非「無路可走」時，同一條規則一樣適用。',
        '一察覺自己把替代品包裝成驗證：停手、講出來、修好。',
        '「跑完沒有報錯」不是證明。證明是只有被要求的行為才產生得出的輸出。',
        '向真相的擁有者讀回。自己的 response body、快取、日誌、截圖，或者操作者一句「看起來沒問題」，都不算讀回。',
        '引用一個 obligation 編號不等於覆蓋了它。說出你預期什麼、觀察到什麼、那個觀察存放在哪裡。',
        '「跑一下相關的測試」不是一份證明計劃。',
        '外部系統的行為由觀察那個系統決定，不由閱讀它的文件或程式碼預設值決定。',
        '在執行之前宣告預期結果，絕不在看見輸出之後。',
        '一次性的執行期觀察，取代不了一條進版控的測試。',
        '兩條分支各自綠燈，不代表合併後綠燈。合併後的 head 未經重新證明之前是未證明的。',
        '絕不修改預期去遷就結果。',
        '一次在開始前就被擋下、或自身驗證失敗的執行，不是通過，也永遠不會變成基準。',
    ]),
    ('沉默與覆蓋面', [
        '寫下「不適用」或「無改動」並附理由。在磁碟上，遺漏和不適用長得一模一樣。',
        '說出「全部」「每一個」或一個數目，就有義務列舉該集合並逐一分類。一次 grep 不是一次列舉。',
        '一個讀不完它應該讀的東西的檢查，不算綠燈。報告你實際覆蓋的範圍。',
        '「沒有東西要報告」是一個正當的結果。不要為了顯得有產出而製造發現。',
        '缺少一份治理文件，不會免除那項義務。照做該步驟，並說明缺少了什麼。',
        '逐行閱讀規格與合約，並在判斷任何片段之前讀完整節。不要略讀然後總結。',
        '用讀過的文件核對計劃與實作；它可以確認原計劃，也可以要求修正。不要把讀取次數當成理解。',
        '新增一個對外寫入、一個授權決定或一個入口，同一次改動要把它寫進 facts 表。'
        '若既有 pattern 未涵蓋它，缺少詞彙可能令依賴 facts 的檢查漏看；先核對現有涵蓋面。',
    ]),
    ('範圍', [
        '以獲批准的結果判斷範圍，不以檔案數目判斷。每一個被改動的檔案都必須是達成該結果所必需。',
        '不得夾帶無關的清理、順手的重新設計、投機性的加固，或以「乾淨重寫」為名的改寫。',
        '在批准範圍以外發現問題：報告並等待指示。不要順手修好。',
        '「改動很小」不是跳過真相來源、範圍、證明或審查的理由。',
        '不要把範圍藏在 commit message 或摘要裡。說出你還做了什麼。',
        '一項改動所需的文件是該改動的一部分，不是日後的清理。標示為清理的改動不得改變行為。',
    ]),
    ('移除', [
        'grep 找不到，以及你不理解某樣東西，都是關於你的搜尋和你的閱讀的事實，不是關於那樣東西的事實。',
        '從未見過一道防護觸發，不是它無用的證據；它年輕的時候更加不是。',
        '移除一道防護之前，說出接手它所覆蓋路徑的機制，以及哪一類缺陷會因此失去負責的機制。',
        '刪除任何無法重新產生的東西之前，先確認別處存有副本。',
        '封存就是刪除然後信任版本控制。不要開一個目錄去裝你不敢刪的東西。',
        '措辭相同、結構相同或主題相同，都不使兩樣東西成為同一份合約。',
        '一條長期被違反而從未被執行的規則，要麼執行它，要麼改掉它。兩樣都不做不是一個選項。',
    ]),
    ('修復', [
        '處理症狀不是修復。同一類失敗出現兩次，代表第一次修在錯的位置。',
        '用同一缺陷類別的另一個實例去修一個缺陷，不是修復。',
        '一個發現要在該事實出現的每一處修好，不只在被報告的那一處。',
        '同一個修復失敗兩次之後不要重播。說出重複出現的根本原因和一個具體的差異，才再嘗試。',
        '每次重試都記下你改了什麼、根本原因、以及學到什麼。「重試了 N 次」不是紀錄。',
        '驗證在結構上不可達時：先修正主張；然後收窄範圍並記錄結轉；升級擺最後。',
        '絕不在一個關鍵失敗之後靜靜地繼續。',
    ]),
    ('主張與發現', [
        '不要報告一個你指不出它真正發生過、亦無證據的失敗模式，包括這個專案的脈絡撐不起的規模風險。',
        '不要因為某條規則這樣說就要求抽象。行為簡單、局部、且不太可能分化時，直接寫程式碼沒有問題。',
        '一個議題一個發現。不要把無關的議題併入一個共同主題。',
        '另一個 agent 的結論不是事實。在據以行動之前，自己至少核實它其中一項說法。',
        '被標示為乾淨的部分和被標示有問題的部分同樣要驗。未被標示的範圍才是盲點。',
    ]),
    ('權威', [
        '遵守使用者與宿主的授權；實作事實對照 code 和測試，文件分工看 docs/README.md。不要用參考資料另立平行權威。',
        '參考資料、比較和範例集屬於資訊。它們不是合約，也不是範本。',
        '舊系統或來源 repo 的文件，不是現況的證據。先對照現行權威解決。',
        '預設更新既有文件。絕不為方便而新增一份平行的權威文件。',
        '絕不保留一份可執行 schema 的第二個散文副本。',
        '`.v4/facts.<repo>.json` 是偵測器的詞彙表，由這個 repo 自報。'
        '框架可以提出標示為 proposed 的草稿，由 repo 核實；檢查器不得把 adopter 的符號寫死進自己裡面。',
    ]),
    ('設計姿態', [
        '從真相的擁有者出發，不從請求的表面出發。接線之前先確定歸屬。',
        '先合約，然後純邏輯，然後轉接層，最後入口表面。',
        '職責與名稱不一致時，跟隨職責。',
        '原則相衝突時：型別安全，然後分離，然後可抽取性，然後命名。',
        '在高風險位置優先收斂舊有程式碼——認證、金錢、對外副作用、擁有真相的狀態轉換。不要見一樣改一樣。',
        # 以下六條原本住在四個 claim kind 的 engagement rule 裡。那四個 kind 在 113 題
        # 實測中示範不到代碼價值而拆走，問題本身沒有失效——所以文字搬到這一層，每個
        # session 問一次，而不再由一部 claim 機器跟著它。
        '一個入口沒有任何認證呼叫：那是一個決定，還是一次遺漏？入口檔裡每個 route handler，都要在它的裝飾器或函式體裡走到一個做認證決定的符號。',
        '這個身分值從哪裡來——客戶端傳入，還是伺服器一側的決定？',
        '簽名驗到了，不代表重播不到。一個錄下來的請求可以永遠重播而簽名仍然有效，因為它曾經有效。說出時間窗有多長，以及哪個 store 記住已經處理過的。',
        '這個值在進入 browser bundle 之前住在哪裡，是誰決定它可以進去？',
        '入口、handler 或面向人的表面裡的狀態改動，不得直接改動 store——沒有裸 DML，沒有 ORM 寫入加 commit；它走那條唯一的編排路徑。',
        '一個地方擁有寫入，才使「改變寫入的方式」變成改一個地方。繞過那一層的寫入，對那層強制的每一條 invariant 而言都是隱形的。',
        '最佳化不得削弱正確性、真相、安全或冪等性。',
        '在最佳化之前先宣告「夠好」是什麼，並把沒有量度的效能主張視為不完整。',
        '必須成立的是那個性質，不是那件產品。不要把供應商或工具變成需求本身。',
        '如果改一個值需要改變行為或改一條測試，它是程式碼，不是設定。',
    ]),
    ('義務與閘', [
        '一項針對性的義務不得變成全面性的。全面性正是它存在來預防的失敗。',
        '容許更嚴格的本地規則。取代或遮蔽一項常設義務則不容許。',
        '決定不承受某個風險，不會刪去它的證明。持久地記下原因。',
        '散文取消不了一項推導出來的義務。指名一個同等強度的替代，或記下一個具型別的理由。',
        '證明要求預設開啟。關掉其中一項是一次寫下來的、一次性的決定，不是逐個任務的判斷。',
        '「工具跑過了」和「閘通過了」是兩個命名空間。絕不合併。',
        '建議性的輸出不是閘的結果，一條測試斷言不是一次安全掃描。',
        '一個在這個 repo 裡跑不起來的閘，比沒有閘更差。不要安裝它。',
        '上游的閘失敗時，跳過依賴它的那個，並記下它被跳過以及原因。',
        '一份標準是地板，不是範本。標題齊全而內容不清楚，仍然是不完整。',
    ]),
    ('工作方法', [
        '開始之前先確認輸入足以完成該工作。缺少一項關鍵輸入：停下來，說出缺了什麼。',
        '把未回答的事情分開：需要判斷的交回擁有者，屬於事實的在計劃可執行之前先答掉。',
        '在依據推斷出來的值行動之前，先把它說出來。',
        '驗收門檻由請求者給出。只有程式碼已經決定了的，才可以推斷。',
        '預設使用較少、較大的工作單位；兩三個檔案的改動很少自成一個單位。只在脈絡上限、需要人作決定、失敗隔離或分開部署時才拆。',
        '不要直接在受保護分支上工作，亦不要在沒有明確許可下執行破壞性的 git 指令。',
        'Commit message 必須對得上它的 diff，並說明為何，不是說明做了什麼。',
        '改動按 repo 已批准的審查與合併政策進入受保護分支；不得把自動檢查或 agent 自述冒稱成人手審查。',
        '已經有一條日誌或紀錄路徑時，不要再發明一條平行的。',
        '框架與工具本身的失敗要連同根本原因記錄下來，不得靜靜繞過。',
    ]),
    # SPEC.md §9 把「唔好用 stopgap」同「唔好 hardcode」派咗畀層 ①,而層 ① 一條
    # 都冇。文件派咗個家,個家冇佢哋 —— 就係呢個 project 一路喺度捉緊嗰個形狀。
    #
    # 其餘幾條係呢個 repo 服務嗰個人嘅常設工程守則,前身 33 份守則從來冇寫低。
    # 一條冇人寫低嘅規則,唔等於一條冇人守嘅規則。
    ('建造', [
        '不要接受一個「暫時頂住、日後再換」的權宜方案（stopgap）。架構決定要為長遠而作。',
        '不要寫死（hardcode）一個系統推導得到的值，也不要寫死一個規則本應涵蓋的個案。',
        '修真正的缺口，不是修它的症狀，也不是修它的報告。',
        '選擇完全滿足當前需求的最簡單實作。不要投機性的抽象、設定或間接層。',
        '不要預設保留向後兼容。移除過時的路徑，而不是加一層兼容、一個 fallback 或一次遷移。',
        '分層生長系統：先做端到端行得通的最小版本，然後每項新能力都疊在一個已經行得通的產品之上。絕不用一個行得通的產品去換未完成的複雜度。',
        '保持元件模組化，關注點清楚分離。',
        '在能降低整體複雜度或提升可靠性的地方，優先採用成熟且有人維護的函式庫。沒有明確理由不要重新實作常見功能。',
        '在自己寫實作或加入套件之前，先倚靠專案裡已有的依賴。不要在未讀過文件與型別之前，假設某個函式庫缺少某項能力。',
    ]),
]

HEADER = """<!-- 由 `v4 doctrine --write` 生成。不要人手修改 —— 改 kernel/doctrine.py
     或這個 repo 的 .v4/claim_kinds.json，然後重新生成。
     `registry-consistency` checker 會比對這個檔案與重新生成的結果。 -->

# {repo} — 開工之前

這份文件是生成的，分兩半：上半永遠一樣，下半由這個 repo 註冊了什麼決定。

**沒有任何機制驗證你讀過這份文件。** 前身驗證過 —— 13,570 次讀取、349 MB、同一批
bytes 讀了 14.2 次，而它改變不了任何事。這裡下的賭注是：一條規則在適用的那一刻出現
一次，比一份要你簽收的文件有用。
"""


#: The generated block's boundary.  Everything outside it belongs to the repo.
#:
#: `write` used to be `p.write_text(render(cfg))` -- the whole file, every time.
#: That holds while the only adopter is this framework, whose CLAUDE.md is
#: nothing but generated. Measured on a real one: `adopter_a` carries 381 lines
#: there, of which about 250 are its own operating contract -- Role, Core
#: Principles, Project Identity, 180 lines of MUST rules -- and a cutover would
#: have deleted them on the first `v4 doctrine --write`.
#:
#: A generated file anybody can edit is a hand-written file with a misleading
#: header; that argument is right and it is about the *generated part*. So the
#: generated part gets a boundary, and `drift` asks its question inside it.
BEGIN = "<!-- v4:doctrine:begin — generated by `v4 doctrine --write`. "\
        "Everything outside these markers is this repo's own. -->"
END = "<!-- v4:doctrine:end -->"


def split(text: str):
    """(before, generated, after).  `generated` is None when there is no block.

    A file with no markers is a repo that had a CLAUDE.md before V4 did. Its
    whole content is `before`, and the block is appended rather than replacing
    it -- which is the case this exists for.

    `BEGIN` with no `END` is not that case: the block is everything from the
    marker to the end of the file, `after` is empty, and `generated` therefore
    does not carry `END`. `write` needs that -- replacing the orphan rather
    than appending a second block under it -- and it is the one shape a caller
    cannot assume away, so `drift` tests for it by name instead of looking for
    `END` in the text a second time.
    """
    i = text.find(BEGIN)
    if i < 0:
        return text, None, ""
    j = text.find(END, i)
    if j < 0:
        return text[:i], text[i:], ""
    return text[:i], text[i:j + len(END)], text[j + len(END):]


def render(cfg) -> str:
    """The repo's doctrine file: standing rules, then what this repo registered."""
    out = [HEADER.format(repo=layout.repo_name(cfg.root)), "", "## 一 · 永遠適用", ""]
    for title, rules in DOCTRINE:
        out.append(f"### {title}")
        out.append("")
        for r in rules:
            out.append(f"- {r}")
        out.append("")

    out += ["## 二 · 這個 repo 的機制", "",
            "以下由這個 repo 現時註冊了什麼生成。", ""]

    kinds = cfg.kinds or {}
    if kinds:
        # Only the kinds something raises. `review-finding` has
        # `detector: null` and no detector emits it -- it exists when a
        # reviewer runs `v4 review add`, which is the one claim a person
        # has to remember. Counting it here told every worker it would
        # arrive by itself.
        raised = [k for k, v in kinds.items() if v.get("detector")]
        out += [f"**{len(raised)} 個 claim kind 配置了 detector。**"
                "是否提出 claim 取決於 scope、facts、註冊狀態和實際掃描；配置存在不等於已經執行。", ""]
        for k, v in sorted(kinds.items()):
            if not v.get("detector"):
                out += [f"`{k}` 唔會自動出現 —— 佢由 `v4 review add` 提出。", ""]

    engaged = sorted((k, v) for k, v in kinds.items() if v.get("engagement"))
    if engaged:
        out += ["### 需要 engagement 的",
                "",
                "這幾個 kind 提出 claim 之後，`v4 check` 不會執行，直到你寫下一句"
                "「這條規則對這段程式碼意味著什麼」。**不是覆述那條規則** —— 規則"
                "已經印在螢幕上。支援的 Write/Edit hook 亦會在初始 gate 未清除、"
                "claims 已可見時要求句子；這不是所有寫入路徑的安全邊界。",
                ""]
        for k, spec in engaged:
            r = spec.get("rule") or []
            for one in (r if isinstance(r, list) else [r]):
                out.append(f"- **`{k}`** — {one.get('text','')}")
        out.append("")

    protected = cfg.protected
    if protected:
        out += ["### 受保護的路徑", "",
                "修改要符合已授權 scope 及必要的 protected-path risk 決定；"
                "超出 scope 時用 `v4 scope widen` 記錄理由。這不是 OS 層面的禁止寫入：",
                "",
                "```", *[f"{p}" for p in protected], "```", ""]

    tc = cfg.config.get("test_command")
    if tc:
        out += ["### 唯一的 test oracle", "",
                f"```\n{tc}\n```", "",
                "核對這條實際命令包含的 filter、測試收集範圍和 skip 行為。"
                "缺少真實環境時，應區分沒有驗證、測試失敗和通過；不要假設每個 repo 都用 pytest markers。", ""]

    out += ["---", "", where_the_documents_are(cfg.root)]
    return "\n".join(out).rstrip() + "\n"


PUBLIC_REPO = "https://github.com/howardc38/vibeproof"


def where_the_documents_are(root) -> str:
    """The one line in this document that says where to go next.

    It said `docs/SPEC.md` and `docs/RATIONALE.md` unconditionally, and in an
    adopter repo neither file is there -- both live in the framework. So the
    single escape hatch in a generated document, written for an agent working
    inside somebody else's repo, named two paths that resolve to nothing, and
    the reader's only recourse was to guess.

    Three answers, in the order they are true:

      the framework itself   `docs/` is right here, so say the relative paths

      an adopter that has    `install.write_launcher` records the framework's
      installed              location in `.v4/home`; the documents are under it,
                             and the absolute path is the thing a reader can
                             actually open

      neither                Say plainly that the two documents live in the
                             framework and give the repo, rather than a path
                             this machine cannot resolve. `.v4/home` is
                             gitignored by design -- it holds one machine's
                             path -- so a clone lands here, and a clone is
                             exactly where somebody is most likely to be lost.
    """
    root = Path(root)
    if (root / "docs" / "SPEC.md").is_file():
        return ("找不到你要的東西：`docs/SPEC.md` 是契約，"
                "`docs/RATIONALE.md` 是為何這樣設計。")
    try:
        home = Path((root / ".v4" / "home").read_text(
            encoding="utf-8").strip())
    except OSError:
        home = None
    if home and (home / "docs" / "SPEC.md").is_file():
        return (f"找不到你要的東西：`{home}/docs/SPEC.md` 是契約，"
                f"`{home}/docs/RATIONALE.md` 是為何這樣設計 —— "
                f"兩份都住在框架，不在這個 repo。")
    return (f"找不到你要的東西：契約是 `docs/SPEC.md`，為何這樣設計是 "
            f"`docs/RATIONALE.md`。兩份都住在框架 repo（{PUBLIC_REPO}），"
            f"不在這個 repo；這台機器上框架的位置寫在 `.v4/home`，"
            f"而那個檔案不進版控，所以一個 clone 要自己寫一次。")


def path_for(repo_root) -> Path:
    return Path(repo_root) / "CLAUDE.md"


#: Words too common to tell one rule from another.
_COMMON = {
    "呢個", "呢條", "嗰個", "一個", "唔係", "係咪", "就係", "而家", "已經", "可以",
    "講出", "或者", "同埋", "但係", "如果", "因為", "所以", "冇有", "唔會", "要",
    "the", "a", "an", "is", "it", "that", "this", "of", "to", "and", "or", "in",
    "for", "on", "not", "be", "as", "at", "by", "with", "from", "you", "your",
    "what", "which", "when", "one", "no", "any", "its", "so", "but", "has",
}


class DoctrineUnreadable(RuntimeError):
    """The rows an uptake audit is about could not be read."""


def uptake(conn, cfg):
    """Which standing rules anybody has actually engaged with.  SPEC.md §9.

    The gap this closes was found by measuring one eight-task run: the rule
    "External write 要留低 audit trail:actor、timestamp、trace id" was on screen
    for all 25 `external-write` engagement sentences, and neither the framework
    arm nor the free arm ever produced a trace id. Nothing noticed, because
    nothing had ever asked whether a stated rule changes anything.

    A rule with many exposures and no engagement is one of three things, and all
    three are worth knowing: it does not apply to this repo, it is written in a
    way nobody can act on, or it is being ignored. Which one it is needs a
    person; that it is happening does not.

    Distinctive terms only. Every rule says "呢個"; the ones that separate a
    rule from its neighbours are what an engaged sentence has to reach for.
    """
    import json as _json
    kinds = cfg.kinds or {}
    said = {}
    try:
        rows = conn.execute(
            "SELECT c.kind, e.payload FROM event e JOIN claim c ON c.id = e.claim_id "
            "WHERE e.kind = 'engagement'").fetchall()
    except Exception as exc:                                     # noqa: BLE001
        # Not `rows = []`. Every sentence this function reports on comes from
        # that query, so an unreadable ledger produced the same answer as a
        # repo where nobody has ever engaged with a rule -- and the row
        # `v4 doctrine --audit` prints from it says "weakest first", which
        # would then be every rule at 0%. The caller cannot tell those apart
        # from the return value, so it does not get one.
        raise DoctrineUnreadable(
            f"the engagement rows this audit is about could not be read "
            f"({type(exc).__name__}: {exc}), so 'nobody engaged with this "
            f"rule' and 'nobody could ask' would print the same") from exc
    for r in rows:
        try:
            p = _json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        if p.get("verdict") == "accepted":
            said.setdefault(r["kind"], []).append(p.get("sentence", ""))

    from .engagement import _tokens
    out = []
    for kind, spec in sorted(kinds.items()):
        rules = spec.get("rule") or []
        rules = rules if isinstance(rules, list) else [rules]
        sentences = said.get(kind, [])
        texts = [r.get("text", "") for r in rules]
        # Distinctive means distinctive. The first version counted any shared
        # token, so "External write 要留低 audit trail:actor、timestamp、trace id"
        # scored 16 of 25 engagements on the strength of `external`, `write` and
        # `id` -- words in every sentence this kind will ever produce -- while
        # the repo contained no trace id at all. A term this kind's other rules
        # also use, or that the kind's own name uses, belongs to the kind.
        shared = set(_tokens(kind))
        for i, t in enumerate(texts):
            for j, other in enumerate(texts):
                if i != j:
                    shared |= _tokens(t) & _tokens(other)
        # `id`, `ok`, `to`: two ASCII letters discriminate nothing, and `id`
        # alone scored three false engagements in a fixture where the sibling
        # rule happened not to use it. One CJK character does carry a word, so
        # the floor is on the Latin ones only.
        def distinctive(t):
            if t in _COMMON or t in shared:
                return False
            return len(t) >= 3 if t.isascii() else len(t) >= 1

        for rule, text in zip(rules, texts):
            terms = {t for t in _tokens(text) if distinctive(t)}
            hits = sum(1 for s in sentences if _tokens(s) & terms) if terms else None
            out.append({"kind": kind, "rule": text, "source": rule.get("source", ""),
                        "terms": sorted(terms),
                        "exposed": len(sentences), "engaged": hits})
    return out


def block(cfg) -> str:
    """The generated section, markers included."""
    return f"{BEGIN}\n\n{render(cfg)}\n{END}\n"


def write(cfg) -> tuple[Path, bool]:
    """(path, changed).  Rewrites the generated block and nothing else."""
    p = path_for(cfg.root)
    old = p.read_text(encoding="utf-8") if p.is_file() else ""
    before, generated, after = split(old)
    new_block = block(cfg)
    if generated is None:
        # No block yet. Append, so a repo that wrote its own CLAUDE.md before
        # this framework arrived keeps every line of it.
        new = (before.rstrip("\n") + "\n\n" + new_block) if before.strip() \
            else new_block
    else:
        # `new_block` ends with `END\n`, and `split` starts `after` at the byte
        # after `END` -- so `after` carries that same newline. Concatenating
        # them added one every time, forever: measured on the reference adopter,
        # 45 trailing blank lines, and `+1` on each of five consecutive writes.
        #
        # `drift` could not see it. It compares `have.rstrip("\n")` against
        # `block(cfg).rstrip("\n")`, so the one thing that was growing is the
        # one thing it strips off both sides -- `v4 doctor` said ok while the
        # file every worker reads before touching anything grew by a line per
        # install.
        #
        # Whitespace-only after the block is the block being last in the file,
        # and one newline is what that means. Anything the adopter actually
        # wrote there is kept byte for byte, which is the case `split` exists
        # for.
        tail = after if after.strip() else "\n"
        new = before + new_block.rstrip("\n") + tail
    if old == new:
        return p, False
    p.write_text(new, encoding="utf-8")
    return p, True


def drift(cfg):
    """None when the file matches what this module would generate.

    A generated file that anybody can edit is a hand-written file with a
    misleading header, so this is what `registry-consistency` asks.

    Opt-in via `"doctrine": true`, and the opt-in has to be explicit rather than
    "every directory with a .v4/ owes one". Inferring it failed every green
    fixture, because a fixture case is a mini-repo with a config and no reason
    to carry standing rules. Once a repo has opted in, deleting the file is a
    finding -- which is the case that made the flag worth having.
    """
    p = path_for(cfg.root)
    if not cfg.config.get("doctrine") and not p.is_file():
        return None
    if not p.is_file():
        return (f"{p.name} is missing and this repo declares `\"doctrine\": true`. "
                f"Run `v4 doctrine --write`.")
    have_text = p.read_text(encoding="utf-8")
    before, generated, after = split(have_text)
    if generated is None:
        return (f"{p.name} carries no generated block. `v4 doctrine --write` "
                f"adds one and leaves everything already there alone.")
    # `split` returns a block for a file carrying `BEGIN` and no `END` -- by
    # design: everything from the marker on is what `write` will replace, and
    # calling it "no block" would make `write` append a second one under the
    # orphaned marker. What that left here was a hole. The doubling check below
    # re-derived the split with `have_text.index(END)`, `index` raises on a
    # string that is not there, and `registry-consistency` calls `drift` inside
    # a `try` that turns anything that is not `ImportError` into "could not
    # check the doctrine file: substring not found". Measured on this repo by
    # deleting the end marker from `CLAUDE.md`: `ValueError` out of a function
    # whose contract is "None, or a sentence saying what drifted".
    #
    # So it is named, and named first: an unterminated block is the case where
    # `v4 doctrine --write` is about to overwrite whatever the adopter wrote
    # below the marker, which is worth more than being told the file "is not
    # what `v4 doctrine` generates".
    if END not in generated:
        return (f"{p.name} opens the generated block at byte "
                f"{have_text.find(BEGIN)} and never closes it -- there is no "
                f"`{END}` after it. Everything from the begin marker to the end "
                f"of the file counts as generated, so `v4 doctrine --write` "
                f"will replace all of it. Put the end marker back first if "
                f"anything below it is this repo's own.")
    have = generated
    want = block(cfg).rstrip("\n")

    # Outside the markers is the adopter's, and that rule is right -- but it
    # also meant nothing noticed when what sat outside was the block itself, a
    # second time. Measured on this repo: 419 lines, 28 `###` headings, 14
    # before the marker and 14 inside, and every one of the 165 non-blank lines
    # before it appearing again within. So `--check` exited 0, `drift` returned
    # None and `doctor` said ok, while what reached a model's context was twice
    # the rules -- and the count is the thing that matters: measured elsewhere,
    # simultaneous rules at 10 are followed 93.8% of the time, at 40 it is
    # 23.8%, at 80 it is 0%. Doubling is not a formatting problem.
    #
    # A quoted rule is not this. Half the block, verbatim, is.
    #
    # `before` and `after` as `split` returned them, rather than finding the
    # markers a second time: two derivations of one boundary is how the second
    # one got to disagree with the first about whether there was a boundary at
    # all.
    outside = [l.rstrip() for l in (before + after).splitlines() if l.strip()]
    lines = [l.rstrip() for l in want.splitlines() if l.strip()]
    if lines:
        echoed = sum(1 for l in lines if l in outside)
        if echoed * 2 > len(lines):
            return (f"{p.name} carries the generated block twice: {echoed} of "
                    f"{len(lines)} generated lines also appear outside the "
                    f"markers. Everything a model reads it for is doubled. "
                    f"Delete the copy outside -- `v4 doctrine --write` only "
                    f"owns what is between the markers.")

    if have.rstrip("\n") == want:
        return None
    return (f"{p.name} is not what `v4 doctrine` generates. Either it was edited "
            f"by hand, or the registry changed and it was not regenerated. "
            f"Run `v4 doctrine --write` and read the diff.")
