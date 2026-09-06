"""The attention checkpoint.  SPEC.md §9.

This is the layer with the weakest evidence behind it, and saying so is part of
the design rather than a caveat on it. Every other mechanism here answers a
defect somebody can point at. This one answers "knew the rule and did it
anyway", which is real -- the author of this file did it repeatedly while
writing it -- but which no measurement in this project isolates.

So it is built to cost almost nothing and to be removable:

  it blocks starting work, never shipping    a refusal before any code is
                                             written costs nothing; a refusal
                                             after twenty minutes of work is a
                                             different mechanism with a
                                             different price

  the tests are mechanical, never a judge     V3 died of a field a framework
                                             filled in for you and then checked
                                             was non-empty. A subjective
                                             reviewer here is worse: it cannot
                                             be satisfied on purpose, so it has
                                             no bound, which is the loop this
                                             whole design exists to remove

  duplicate detection spans every task        V3's failure was one sentence
                                             appearing 38,392 times. A rule
                                             scoped to the current task cannot
                                             see that, and with one to three
                                             sentences per task it is
                                             vacuously true

None of the six tests below is hard to satisfy on purpose. Paste a symbol
name, write forty characters, and it passes. That is understood: the intent is
to make the moment happen, not to prove it did.

This layer stays. It was written with a deletion condition attached -- no
external signal, delete it -- and the repo owner has since decided the other
way, twice and in as many words. The condition is gone rather than left
unmet: a standing "should be deleted" inside the thing itself is an argument
anybody reading this file inherits, and it outlived the decision it was
waiting on.
"""

import json
import re
import sys
from datetime import datetime, timezone

from . import ledger as ledger_mod
from .ledger import insert

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[一-鿿]")


#: What the analysis layer writes when a finding belongs to a file rather
#: than to a symbol inside it.  Named once here so the rule that skips it can
#: say why.
MODULE_SYMBOL = "<module>"

def _tokens(text):
    return set(WORD.findall(text.lower()))


def _rule_text(r):
    return (r.get("text", "") if isinstance(r, dict) else r) or ""


#: Containment at which a sentence is a reproduction rather than an answer.
#:
#: 1.0, and the paragraph in `recited` says why: padding cannot lower
#: containment, so every copy scores exactly this, and everything short of it
#: is a sentence this measure cannot classify.
RECITED_FLOOR = 1.0


def recited(text, rules) -> float:
    """How much of a rule this sentence reproduces, 0..1.

    `overlap` is Jaccard, and Jaccard falls when a sentence gets longer, so
    pasting a rule back and appending walks straight through it: measured,
    a verbatim copy scored 1.0 and nine characters of "呢個好重要要小心處理"
    brought it to 0.78 and through. Padding defeated the one test that was
    supposed to be about not padding.

    This asks the containment question instead -- what fraction of the rule's
    own words came back -- and no amount of appending lowers it. Measured on
    this repo's rules: copies score 1.00 whatever is stapled on, and sentences
    written about the code score 0.08-0.19, because a sentence about this
    `fetch()` and this `__cause__` does not reuse a rule's vocabulary even when
    it is about the same subject.

    And the threshold is `RECITED_FLOOR`, not `dup_threshold`. That number
    answers a different question and borrowing it cost real sentences: a rule
    short enough to be a *question* -- `環境變數注入係平台要求,定係方便?`, 14
    tokens -- can only be answered in its own nouns, and a true answer to it
    measured 0.93. Three candidate repairs were measured and two failed: taking
    the claim's own words out of the denominator left it at 0.88, and requiring
    the sentence to be mostly rule words let a long-padded copy through at 0.40,
    below the 0.42 that a real sentence in this repo already scores.

    What separates them is that padding cannot lower containment -- which is the
    property this measure was chosen for. Measured: a copy is 1.00, a copy plus
    nine characters is 1.00, a copy plus a long tail is 1.00, while 252 accepted
    sentences here reach 0.60 and the adopter's true answers reach 0.93. So the
    line that holds is total reproduction and nothing short of it. A copy with
    one word changed scores what a real answer scores, and this does not pretend
    to tell those apart.
    """
    t = _tokens(text)
    best = 0.0
    for r in rules or ():
        rt = _tokens(_rule_text(r))
        if rt:
            best = max(best, len(t & rt) / len(rt))
    return best


def overlap(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def history(conn, unlike=None):
    """Every accepted sentence, for the duplicate check.

    `unlike` is a claim row whose *coordinates* -- kind, file, symbol -- are to
    be left out. The failure this corpus exists to catch is V3's: one sentence
    appearing 38,392 times across claims that had nothing to do with each
    other. A worker meeting the same claim again -- reopened after an abandon,
    re-derived on a new task -- is the opposite case: the same code, the same
    true reason, and the only way past was to reword until the tokens differed,
    which is the filler this guard is against, produced by the guard.

    Coordinates and not the claim id, because a reopened task derives a new id
    for the same finding: the id is where it was raised, the coordinates are
    what it is about.
    """
    sql = ("SELECT e.payload FROM event e "
           "LEFT JOIN claim c ON c.id = e.claim_id "
           "WHERE e.kind = 'engagement'")
    args = ()
    if unlike is not None:
        sql += (" AND NOT (IFNULL(c.kind,'') = ? AND IFNULL(c.file,'') = ? "
                "AND IFNULL(c.symbol,'') = ?)")
        args = (unlike["kind"] or "", unlike["file"] or "", unlike["symbol"] or "")
    out = []
    for r in conn.execute(sql, args):
        p = json.loads(r["payload"])
        if p.get("verdict") == "accepted":
            out.append(p["sentence"])
    return out


def rule_for(cfg, claim_row):
    """The clause this claim's kind is about, and where it comes from.

    Engagement without this was a request for forty characters. It stopped work
    and named nothing, so the only thing a worker could engage with was the
    question, which the kernel already generated. Carrying the clause is what
    makes the pause about something.

    It is deliberately one or two sentences. The corpus this comes from is
    3,400 lines, and V3's answer to that was to make you prove you had read it
    -- 13,570 times, 349 MB, and 96.7% of the conclusions were filled in by the
    framework. Showing two sentences at the moment they apply is the opposite
    move.
    """
    r = cfg.kinds.get(claim_row["kind"], {}).get("rule")
    if r is None:
        return None
    # One kind, several rules. The schema took a single `rule` object, and a
    # line-by-line read of the predecessor's guidelines put five distinct
    # obligations on `external-write`, four on `secret`, four on
    # `review-finding` -- so the shape itself was discarding thirteen of
    # twenty-five. A list is not a feature; it is the absence of a limit that
    # was never argued for.
    return r if isinstance(r, list) else [r]


def _credential_in(text: str, cfg=None):
    """The literal a secret scanner would call a credential, or None.

    Judged by the same classifier `checkers/secret_scan.py` uses, so a shape it
    already treats as a placeholder passes here too. Measured over 73 real
    sentences: three reached the scanner, all three were classified as
    placeholders, and none would have been refused -- so this is not a tax on
    talking about credentials, which is what several of these rules ask for.
    """
    # Both failure paths returned `None` and printed nothing, so a silently
    # disabled guard and a clean sentence gave the same answer -- and the
    # sentence then goes into an append-only table that cannot be corrected and
    # is exported to a committed file. The refusal this feeds spells the cost
    # out: "a sentence like this stops the ledger from being committable, hours
    # after it was written and with nothing pointing back here." A check that
    # cannot run still must not refuse the sentence; it must not be invisible
    # either.
    try:
        from .analysis import secret_patterns
    except Exception as exc:                                    # noqa: BLE001
        print(f"v4: the credential guard on this sentence did not run "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return None
    try:
        # The repo's table, not the shipped one. The docstring above says this
        # is judged "by the same classifier `checkers/secret_scan.py` uses" and
        # it was not: that checker unions `.v4/secret_patterns.json` in and this
        # never saw it, so an adopter's own credential families passed here and
        # were caught later by `secret` -- after the sentence was in an
        # append-only table and on its way into a committed file.
        # `getattr`, because a caller without a root is asking about the
        # shipped families and that is a real answer -- narrower than the
        # repo's, and it must not take the guard down with it. Losing the whole
        # check to reach for a wider table is the trade this function's own
        # failure paths already refuse.
        root = getattr(cfg, "root", None)
        table = (secret_patterns.table_for(root) if root is not None
                 else secret_patterns.PATTERNS)
        report = secret_patterns.analyse_source(text, path="engagement",
                                                table=table)
    except Exception as exc:                                    # noqa: BLE001
        print(f"v4: the credential guard on this sentence did not run "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return None
    return report.findings[0].candidate.text if report.findings else None


def judge_text(cfg, *, sentence, subject_words, rules=(),
               off_subject="mentions none of the paths it is about", rule=None):
    """The five tests that need nothing but a sentence and what it is about.

    Five: empty, under `min_chars`, naming neither file nor symbol, carrying a
    live-looking credential, reciting the rule back. This line said "four" and
    the body has implemented five since the credential test was added below it,
    while the last paragraph said seven -- three numbers for one set, in one
    docstring, and the count is the only part of it a reader can act on.

    `v4 scope widen` needs exactly this: a sentence about paths rather than
    about a claim. `judge` needs it too, and for two revisions did not use it --
    the docstring said "sharing the rules means the two places cannot drift into
    different standards for the same act of writing one sentence" while the two
    places each carried their own copy, and they had already drifted: `judge`
    grew a `<module>` placeholder rule from a measured failure and this did not.

    That is `R-NO-ALGO-DUP`, one of the predecessor's three structural rules and
    one of the two V4 did not adopt, happening inside the module that implements
    V4's rule layering.

    Seven grounds refuse a sentence in this module, not six -- five of them
    here and two in `judge`, which is why no one function shows all seven.
    Here: empty, under `min_chars`, naming neither file nor symbol, reciting
    the rule, and carrying what a secret scanner reads as a live credential.
    In `judge`: the question recited back, and overlapping history.
    SPEC.md section 9 headed its table with six and said twice more
    that all six are literally satisfiable, so the count a reader carried away
    was one short of the gate they were about to meet -- and the missing one is
    the only one whose cost lands hours later, when `v4 ship` writes this
    sentence verbatim into a committed, scanned file.
    """
    th = cfg.thresholds
    text = (sentence or "").strip()
    if not text:
        return False, "nothing was written"
    if len(text) < th["min_chars"]:
        return False, (f"{len(text)} characters, and the floor is {th['min_chars']}. "
                       f"A field that accepts anything gets filled with anything.")
    words = {w.lower() for s2 in subject_words
             for w in str(s2).replace("/", " ").replace(".", " ").split() if len(w) > 2}
    if words and not (_tokens(text) & words):
        return False, off_subject
    leaked = _credential_in(text, cfg)
    if leaked:
        return False, (
            f"this sentence contains {leaked!r}, which the secret scanner reads "
            f"as a real credential rather than a shape being discussed.\n\n"
            f"`v4 ship` writes engagement prose verbatim into "
            f"`.v4/ledger_export.jsonl`, and that file is committed and "
            f"scanned -- so a sentence like this stops the ledger from being "
            f"committable, hours after it was written and with nothing pointing "
            f"back here. Measured: the `secret-chain` rule asks a worker to say "
            f"why a handler does not wrap a token, a good answer named the "
            f"`https://user:token@` form, and answering the claim well made the "
            f"ledger uncommittable.\n\n"
            f"Describe the shape without a literal, or -- if this pair really is "
            f"a placeholder -- declare it as one and write the sentence again.")
    # `rule=` takes either the rule object or its text; `rules=` takes the list.
    # Two callers passing different shapes into one function is the next
    # divergence, and it arrived within a minute of the second caller existing.
    all_rules = list(rules)
    if rule is not None:
        all_rules.append(rule.get("text", "") if isinstance(rule, dict) else rule)
    all_rules = [r for r in all_rules if r]
    if all_rules:
        back = recited(text, all_rules)
        if back >= RECITED_FLOOR:
            return False, (f"{round(back * 100)}% of one rule's own words came "
                           f"back. They are already on screen; what is being "
                           f"asked is what they mean for this code, which is the "
                           f"part nobody else can write. Appending to a copy does "
                           f"not change this number, which is why it replaced the "
                           f"one it could.")
    return True, "accepted"


def _subject_words(claim_row) -> set:
    """What a sentence has to name, tokenised the way a sentence is.

    The two sides were written independently and drifted. The subject split on
    `/` and `.` only, so `social-ops` stayed one word; the sentence goes through
    `WORD`, which stops at the hyphen and yields `social` and `ops`. `judge`
    intersects them, and for a hyphenated filename that intersection is empty
    for every sentence anybody can write -- so the gate refuses, says "name the
    file or the symbol", and refuses the sentence that does. A `review-finding`
    on such a file could then only ever be signed.

    Reported from an adopter, reproduced here: subject `{'social-ops'}` against
    `['at', 'could', 'launcher', 'not', 'ops', 'run', 'social', 'the']`.

    It is already in this file, attributed to something else. The comment below
    records `dep-provenance` refusing three sentences in a row "each of which
    named the actual file" and blames the `<module>` placeholder --
    `dep-provenance` is hyphenated, and it reproduces for this reason too. The
    placeholder repair landed and the cause stayed.

    One tokeniser, so the two cannot disagree again: whatever `WORD` admits on
    one side it admits on the other. The `len(w) > 2` floor stays -- `py` and
    `go` are suffixes, not subjects.

    `<module>` is what the analysis layer writes when a finding belongs to a
    file rather than to anything inside it, and a repo-scoped claim carries no
    file at all. Together that leaves the placeholder as the only nameable
    thing, and the tokeniser strips its angle brackets, so it can never be
    named. A placeholder is not a subject.
    """
    words = {w for w in _tokens(claim_row["file"] or "") if len(w) > 2}
    symbol = claim_row["symbol"]
    if symbol and symbol != MODULE_SYMBOL:
        words |= {w for w in _tokens(symbol) if len(w) > 2}
    return words


def judge(conn, cfg, *, claim_row, sentence, exempt_duplicate=False):
    """(ok, reason).  Mechanical only -- there is no reviewer here on purpose."""
    th = cfg.thresholds
    text = (sentence or "").strip()

    template = cfg.kind(claim_row["kind"]).get("question_template", "")
    if text == template.strip():
        return False, "this is the question restated, not a reading of it"

    subject_words = _subject_words(claim_row)
    # `<module>` is what the analysis layer writes when a finding belongs to a
    # file rather than to anything inside it, and a repo-scoped claim carries no
    # file at all. Together that leaves `{"<module>"}` as the only thing a
    # sentence could name -- and the tokeniser strips the angle brackets, so it
    # can never be named. Measured on a live repo: `dep-provenance` refused
    # three sentences in a row, each of which named the actual file, with the
    # advice "name the file or the symbol". A placeholder is not a subject.

    # The five tests that need nothing but the sentence and its subject -- the
    # same five `judge_text` names, counted the same way, because this comment
    # said "four" from the same day that docstring did. One implementation, so
    # `scope widen` and this cannot hold a sentence to two different standards
    # -- which they already did. The two grounds above and below this call are
    # what make seven.
    ok, why = judge_text(cfg, sentence=sentence, subject_words=subject_words,
                         rules=rule_for(cfg, claim_row) or [],
                         off_subject="mentions nothing this claim is about -- "
                                     "name the file or the symbol")
    if not ok:
        return False, why

    if not exempt_duplicate:
        for prior in history(conn, unlike=claim_row):
            if overlap(text, prior) > th["dup_threshold"]:
                return False, (f"reads as a repeat of an earlier sentence "
                               f"(overlap over {th['dup_threshold']}). The failure "
                               f"this guards against is one sentence appearing "
                               f"thousands of times, so the comparison spans every "
                               f"task, not this one.")
    return True, "accepted"


def record(conn, *, task_id, claim_id, sentence, verdict, reason):
    insert(conn, "event", task_id=task_id, claim_id=claim_id, kind="engagement",
           actor="worker",
           payload={"sentence": sentence, "verdict": verdict, "reason": reason},
           created_at=datetime.now(timezone.utc).isoformat())


#: A sentence written against a kind before any claim of that kind exists.
BEFORE_KIND = "engagement_before"


#: Who a pre-work sentence came from. Self-reported, like every other `actor`
#: in this ledger -- `worker`, `person`, `hook` and `kernel` are all set at the
#: call site and none of them is evidence. It is here because without it the one
#: question this event exists to answer cannot be asked at all.
#:
#: A subset of `ledger.ACTORS`, and named from it: this was `("splitter",
#: "worker", "human")`, a second enumeration of one vocabulary, and it is why
#: `v4 engage --actor human` wrote a word no other writer used. Narrower than
#: the full set on purpose -- a `hook` or the `kernel` does not write a sentence
#: before the work, and offering them as choices would suggest they could.
BEFORE_ACTORS = ("splitter", "worker", ledger_mod.PERSON)


def judge_before(conn, cfg, *, task_id, kind, sentence, actor="worker"):
    """The sentence a kind gets before the work, when no claim can exist yet.

    Two shapes of claim, and only one of them can be answered first. A claim
    about code that already exists is raised at the first derive -- measured, 37
    of 43 across six tasks -- and the write hook now holds every write until
    those have sentences. A claim about code this task is *creating* cannot
    exist until the code does, because the detector reads the tree. Measured:
    `fail-closed` on a new worker's `_send` was raised seventeen minutes after
    the file was first written.

    So for that half the sentence cannot be about a claim, and it does not have
    to be: the rule exists in `claim_kinds.json` with no claim anywhere. What it
    is about is what this task is going to build, and the mechanical test is the
    same one every other sentence gets -- name something you are working on.
    The subject is the task's scope, because that is what "the code I am about
    to write" resolves to before any of it exists.

    Judged, not just stored: an unjudged field gets filled with anything, and
    the six sentences this exists to move earlier were all well-argued and all
    wrong. Writing one first does not make it right. It makes it a position on
    the record before the code, which is the thing that was missing -- afterwards
    the claim's own sentence can be read against it.

    `actor` decides whether that reading means anything. The argument for this
    event is that a sentence handed down by whoever cut the task is a constraint,
    while the same sentence from the agent about to write the code is the same
    self-justification a few minutes earlier -- and that is the shape six
    measured sentences already took. Recorded as `worker` for everyone, the
    difference is not askable. It was hardcoded that way for one day, and three
    tasks wrote pre-work sentences whose author cannot now be established.

    Self-reported, and no more than that.
    """
    if actor not in BEFORE_ACTORS:
        return False, (f"actor {actor!r} is not one of {', '.join(BEFORE_ACTORS)}. "
                       f"Who wrote this decides whether it is a constraint or a "
                       f"rehearsal.")
    rules = (cfg.kinds.get(kind) or {}).get("rule") or []
    if not rules:
        return False, (f"{kind} carries no rule, so there is nothing to engage "
                       f"with before the work. Kinds that do: "
                       f"{', '.join(sorted(k for k, v in cfg.kinds.items() if (v or {}).get('rule')))}")
    row = conn.execute("SELECT scope_globs FROM task WHERE id = ?",
                       (task_id,)).fetchone()
    if row is None:
        return False, f"no such task: {task_id}"
    ok, reason = judge_text(
        cfg, sentence=sentence, subject_words=set(json.loads(row["scope_globs"])),
        rules=rules,
        off_subject=("mentions nothing this task is scoped to. Before the code "
                     "exists, what a rule is about is where it is going to be "
                     "written."))
    insert(conn, "event", task_id=task_id, claim_id=None, kind=BEFORE_KIND,
           actor=actor,
           payload={"claim_kind": kind, "sentence": sentence, "actor": actor,
                    "verdict": "accepted" if ok else "refused", "reason": reason},
           created_at=datetime.now(timezone.utc).isoformat())
    return ok, reason


def before_the_work(conn, task_id, kind=None):
    """Accepted pre-work sentences for a task: `{kind: (actor, sentence)}`.

    The actor rides along because the sentence alone does not say whether it was
    handed to the worker or written by it.
    """
    out = {}
    for r in conn.execute(
        "SELECT actor, payload FROM event WHERE task_id = ? AND kind = ? ORDER BY id",
        (task_id, BEFORE_KIND),
    ):
        p = json.loads(r["payload"])
        if p.get("verdict") == "accepted":
            out[p.get("claim_kind")] = (p.get("actor") or r["actor"],
                                        p.get("sentence"))
    return out if kind is None else out.get(kind)


def accepted_for(conn, claim_id):
    row = conn.execute(
        "SELECT payload FROM event WHERE claim_id = ? AND kind = 'engagement' "
        "ORDER BY id DESC LIMIT 1", (claim_id,)).fetchone()
    if not row:
        return None
    p = json.loads(row["payload"])
    return p["sentence"] if p.get("verdict") == "accepted" else None


def required_for(cfg, claim_row):
    return bool(cfg.kinds.get(claim_row["kind"], {}).get("engagement"))
