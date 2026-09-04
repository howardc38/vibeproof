"""Does this shell command write somewhere it may not?  SPEC.md §5.

The write hook watches Write/Edit/MultiEdit. One `sed -i .v4/config.json`
goes nowhere near it, and `.v4/config.json` names the sole test oracle.

The predecessor solved this and the judgement worth carrying is not the list of
commands -- it is how a real redirection is told from the character `>` inside a
string. It is not a regex: the tokenizer decides at tokenise time, in one place,
and every consumer after that trusts the token kind rather than guessing again.
So `echo "a > b"` produces one word and zero redirects, while `echo a > b`
produces a redirect, and neither needs a heuristic.

Deliberately not a sandbox. Unsupported syntax fails closed only when the
command also mentions a protected path -- that conjunction is what keeps
`for f in *; do echo $f; done` from being refused. The predecessor measured what
happens without it: 100% of its blocking came from two bookkeeping gates.

**A guard that recognises writes only guards against the writes it recognises,
and the ways to spell a write do not end.** Four were measured walking straight
through against this repo's own protected set, and not one of them was an idea
the roll call had missed -- they were spellings of writes already in it:
`python3 -c "open('.v4/config.json','w')"` and `node -e` (no interpreter can be
judged from its argv at all), `rm /abs/path/to/.v4/config.json` (the same file,
spelled from the root), `git -C . rm` (a global option carrying a value ate the
subcommand), `sed --in-place` and `perl -pi -e` (one flag, three spellings).
Four more rows would have left the fifth, and `sudo`, `xargs` and `env` were
sitting there untried.

So the question each segment answers is no longer "is this a write" but
**"can this guard say it is only a read"**, and there are three answers rather
than two: a write it can name, a command it knows to be read-only, and
everything else -- which reaches the same `None` an unparseable line reaches.
The roll call that has to be complete is `READERS` now, and that is the whole
repair: an omission there costs a refusal that says why, where an omission in
`WRITERS` cost silence. `WRITERS` and `INPLACE` survive to name the file in the
message; they no longer decide.

The bill: `sed -n 1,20p checkers/scope.py` is refused, because sed cannot be
read as a reader without trusting a flag spelling again. `cat checkers/scope.py
| sed -n 1,20p` is not -- the protected path sits in the reading segment. That
is the rewrite the hook's refusal asks for.
"""

import re
from fnmatch import fnmatch

#: Which arguments a command writes. This was one integer, an offset into the
#: argument list, and an offset cannot say both of the true things: `rm a b`
#: removes both, so "the last one" was wrong for five of these, and `cp a b dst`
#: only writes `dst`, so "all of them" is wrong for the other three. Measured
#: with the offset in place: `rm .v4/config.json README.md` reported nothing,
#: because adding any second argument moved the protected one out of `args[-1:]`.
ALL_ARGS, LAST_ARG = "all", "last"
#: Commands that write to a path given as an argument.
WRITERS = {
    "tee": ALL_ARGS, "rm": ALL_ARGS, "shred": ALL_ARGS, "truncate": ALL_ARGS,
    "chmod": ALL_ARGS, "chown": ALL_ARGS,
    # `mv` is here rather than with `cp` because it destroys what it moves: the
    # sources are gone afterwards, which is the same loss `rm` causes.
    "mv": ALL_ARGS,
    # These read every argument but the last and write only that one, so
    # flagging the sources would report a read as a write.
    "cp": LAST_ARG, "install": LAST_ARG, "ln": LAST_ARG,
    # `dd` names its destination in `of=`; the branch below owns it. Listed so
    # the name is known rather than silently absent.
    "dd": LAST_ARG,
    # Every one of these was measured walking straight through: `touch
    # .v4/config.json`, `mkdir -p .v4/foo` and `patch -p1 checkers/scope.py <
    # /tmp/p.diff` all returned `[]`. A whitelist of writers is only as good as
    # the roll call, which is the argument SPEC.md §10 makes against whitelists
    # generally -- and this one is no longer what refuses. An unrecognised
    # command naming a protected path is refused below whether or not it is
    # here, so a row buys the sentence that names the file, not the refusal.
    "touch": ALL_ARGS, "mkdir": ALL_ARGS, "rmdir": ALL_ARGS,
    "patch": ALL_ARGS,
}

#: Commands that take a path and never write to it. **This is the roll call
#: that has to be complete now**, and it is complete in the direction that is
#: safe to be wrong in: a reader missing from here is refused with a sentence
#: saying the guard could not tell, which somebody reads and fixes.
#:
#: Absent on purpose, each because it can write the path it is handed and the
#: only thing separating read from write is a flag spelling -- the failure this
#: whole module was rewritten out of: `sed`, `perl`, `awk` and `ruby` (`-i`,
#: `-pi`, `-i.bak`, `--in-place`); `sort` (`-o FILE`); `uniq` (a second operand
#: is an output file); `find` (`-delete`, `-exec`); `fd` (`-x`); `yq` (`-i`);
#: `curl` and `wget` (`-o`, `-O`); `command`, `env`, `xargs` and `sudo` (they
#: run something else); every interpreter there is.
READERS = frozenset(
    "cat head tail less more nl od xxd strings file stat wc column "
    "grep egrep fgrep rg ag ack ls tree du df diff cmp comm cut tr fold "
    "basename dirname realpath readlink echo printf pwd which type "
    "true false test md5 md5sum sha1sum sha256sum shasum cksum jq".split())

#: Multiplexers: the first word writes nothing, the second decides. `git` was
#: absent entirely, so `git checkout -- .v4/config.json`, `git restore .v4/`
#: and `git rm .v4/config.json` all reported `[]` -- three ways to revert or
#: delete the files that judge the work, through the one route this guard
#: exists to watch. Listing `git` under `ALL_ARGS` would report `git status
#: .v4` as a write, so the subcommand is read.
SUBCOMMAND_WRITERS = {
    "git": {"checkout", "restore", "rm", "mv", "apply", "clean", "stash"},
    # `v4 export --out <path>` writes the path it is handed, and it is the only
    # thing `v4` does that does. See `SUBCOMMAND_DEFAULT_READS`.
    "v4": {"export"},
}

#: The other half of the same question, and the half that now decides. `git
#: status .v4` has to stay free, so `git` cannot simply be unclassified; a
#: subcommand that is on neither list is, and is refused.
SUBCOMMAND_READERS = {
    "git": {"status", "log", "diff", "show", "grep", "blame", "ls-files",
            "ls-tree", "cat-file", "rev-parse", "rev-list", "describe",
            "shortlog", "whatchanged", "diff-tree", "name-rev"},
}

#: Multiplexers whose *unlisted* verbs read. One entry, and it is this
#: framework's own front door.
#:
#: `v4` carries protected paths in its argv constantly and by design -- `scope
#: widen --add .v4/x`, a `--why` or a `--note` that quotes the file it is
#: about -- and the refusal `hooks/bash_guard.py` prints *is* a `v4 scope widen
#: --add <the protected path>`. Left unclassified, this guard refused its own
#: remedy: measured while writing the repair, the widen that let it land was
#: itself a command the new rule denies.
#:
#: Not the same judgement as `git`, where an unknown verb stays unknown. `git`
#: has a hundred and fifty verbs and most of them write. `v4` writes `.v4/` on
#: nearly every run and never from a path in its argv -- it goes through the
#: kernel, which is the route this guard exists to keep writes on, and a widen
#: into a protected path is gated by a signature rather than by this hook.
SUBCOMMAND_DEFAULT_READS = ("v4",)

#: Global options that consume the word after them, per multiplexer. Without
#: these the subcommand was "the first word that does not start with `-`", so
#: `git -C . rm .v4/config.json` read `.` as the subcommand, matched nothing on
#: either list, and reported no write -- while `git rm` on the same file was
#: refused. `v4 --repo . export --out .v4/x` is the same shape and was measured
#: the same way.
#:
#: The `git` half is allowed to be incomplete: an option it does not know
#: shifts the subcommand onto a word on neither list, and that refuses. The
#: `v4` half is not, because `v4`'s unlisted verbs read -- so its two are the
#: two `bin/v4` declares, and a third would have to arrive with a row here.
VALUE_OPTS = {
    "git": ("-C", "-c", "--git-dir", "--work-tree", "--namespace",
            "--exec-path", "--super-prefix", "--attr-source", "--config-env"),
    "v4": ("--repo", "--acceptance"),
}

#: `cp -t DIR SRC...` and `install -t DIR SRC...` put the destination first.
#: `LAST_ARG` picked the source: measured, `cp -t .v4 /tmp/config.json`
#: returned `[]` because `operands[-1:]` is the file being read.
TARGET_FLAGS = ("-t", "--target-directory")
#: In-place editors: the file is an argument, and the flag is what makes it a
#: write. None of them is in `READERS`, so what `_in_place` decides is which
#: sentence the refusal carries, not whether there is one.
INPLACE = ("sed", "perl", "awk", "ruby")

UNSUPPORTED = ("$(", "`", "eval ", "<<", "|&")


def tokenize(cmd: str):
    """[(kind, text)] with kind in {'word', 'op'}.

    Quoted text is always a word. The check happens before the operator scanner
    runs, so a `>` inside quotes cannot become an operator -- which is the whole
    reason this is a state machine and not a pattern.
    """
    out, buf, quote, i = [], [], "", 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            if ch == quote:
                quote = ""
            else:
                buf.append(ch)
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "\\" and i + 1 < len(cmd):
            buf.append(cmd[i + 1])
            i += 2
            continue
        if ch == "\n":
            # A newline separates commands the way `;` does. It went to the
            # whitespace branch, so it produced no operator, every line of a
            # multi-line payload landed in one segment, and only `words[0]` of
            # the first line was ever read as a command name. Measured:
            # `sed -i s/a/b/ .v4/config.json` was reported and
            # `echo hi\nsed -i s/a/b/ .v4/config.json` was not -- prefixing any
            # harmless first line defeated this guard entirely, and Bash tool
            # payloads are routinely multi-line.
            if buf:
                out.append(("word", "".join(buf)))
                buf = []
            out.append(("op", ";"))
            i += 1
            continue
        if ch.isspace():
            if buf:
                out.append(("word", "".join(buf)))
                buf = []
            i += 1
            continue
        if ch in "><|;&":
            if buf:
                out.append(("word", "".join(buf)))
                buf = []
            j = i
            while j < len(cmd) and cmd[j] in "><|;&":
                j += 1
            out.append(("op", cmd[i:j]))
            i = j
            continue
        buf.append(ch)
        i += 1
    if quote:
        return None                       # unbalanced: caller fails closed
    if buf:
        out.append(("word", "".join(buf)))
    return out


def _spellings(word: str):
    """Every repo-relative path this one argv word could be naming.

    `subject_files.matches` says in its own docstring that it takes a
    repo-relative path, and an argv word is not one -- it is a *spelling* of a
    path, and the shell has several. Handing the word straight over is what
    made `rm .v4/config.json` refused and `rm /Users/me/repo/.v4/config.json`
    allowed: the same file, the same command, one of them from the root.

    So the word is normalised into candidates and the owner is asked about each,
    which is a different thing from a second matcher. Every candidate only ever
    adds a refusal.

    Three sources of spelling, all measured against this repo's set:

    * a leading path -- `/Users/me/repo/.v4/x`, `../repo/.v4/x`, `$PWD/.v4/x`.
      Resolving against the repo root would answer the first and not the other
      two, and would answer it wrongly for a sibling worktree of the same repo,
      which is where the finding was reproduced. Every `/`-suffix covers all
      three, and does not need to know where the root is.
    * a value carried on an option -- `--reference=.v4/config.json`.
    * a path buried in something that is one word to the tokenizer, because it
      arrived quoted: `bash -c "rm .v4/config.json"`, and
      `python3 -c "open('.v4/config.json','w')"` -- the second is the finding,
      and splitting on whitespace alone left the path glued to `open('`.

    The run of path characters is what is lifted out, which is the same reading
    the whole-command mention test already did on raw text; it is one function
    now rather than two spellings of one rule.

    A carved-out chunk has to look like a path, and a word the tokenizer already
    isolated does not. That distinction was missing, and prose paid for it:
    `git commit -m 'rework the checkers and detectors story'` is one word to the
    tokenizer, was carved into six, and `checkers` matched `checkers/**` -- so
    the message was refused for the words in it, with no path anywhere in the
    command. Measured: that exact command DENY, the same command with those two
    words changed ALLOW. An adopter hit it twice writing commit messages, and
    this file's own history has it a third time.

    So a chunk lifted *out of* a word carries a separator or it is not a path.
    Every case the three sources above were written for keeps working, because
    each of them is a real path and real paths have separators:
    `bash -c "rm .v4/config.json"`, `python3 -c "open('.v4/config.json','w')"`,
    `--reference=.v4/config.json`. A word with no whitespace is not carved at
    all, so `rm checkers` and `rm -rf checkers` are untouched.

    What this gives up, said rather than left to be discovered: a protected glob
    with no separator of its own -- `Makefile`, not `.v4/**` -- named inside a
    quoted sub-command, `bash -c "rm Makefile"`. Neither this repo nor the
    reference adopter has such a glob; all eight in the two of them are
    `something/**`. `tests/test_a_word_is_not_a_path.py` asserts the miss rather
    than describing it.
    """
    text = str(word).replace("\\", "/")
    #: More than one whitespace-separated token means this is being carved up:
    #: a quoted argument, or the whole command line on the unparseable path.
    carved = len(text.split()) > 1
    out = set()
    for chunk in re.findall(r"[\w./*+-]+", text):
        if carved and "/" not in chunk:
            continue
        parts = chunk.split("/")
        out.update("/".join(parts[i:]) for i in range(len(parts)))
    return out


def _protected(path: str, globs) -> bool:
    """Could this argv word name a protected path?  `subject_files.matches`
    decides, once per spelling `_spellings` can read out of the word.
    """
    from .subject_files import matches
    return any(matches(s, globs) for s in _spellings(path))


def _subcommand(cmdname, args):
    """A multiplexer's verb, past the global options that carry a value."""
    opts, i = VALUE_OPTS.get(cmdname, ()), 0
    while i < len(args):
        if not args[i].startswith("-"):
            return args[i]
        i += 2 if args[i] in opts else 1
    return None


def _in_place(args) -> bool:
    """Does this `sed`/`perl`/`awk`/`ruby` invocation edit its file in place?

    The old test was `arg.startswith("-i")`, which is one of the spellings.
    `perl -pi -e`, `sed -i.bak`, `sed -ni` and `sed --in-place` are the others,
    and all four were measured returning `[]` on a file `sed -i` was refused on.

    So the short form is read as a cluster of letters rather than a prefix, and
    `-Ilib` is read as in-place by it -- a refusal on a `perl` that only reads,
    which is the direction to be wrong in. The command is not in `READERS`
    either way, so a spelling this misses is still refused; only the sentence
    changes.
    """
    for a in args:
        if not a.startswith("-") or a == "-":
            continue
        opt = a.lstrip("-").split("=", 1)[0].split(".", 1)[0]
        if a.startswith("--"):
            if opt in ("in-place", "inplace"):
                return True
        elif "i" in opt:
            return True
    return False


def _target_flag(args):
    """The destination named by `-t DIR` / `--target-directory=DIR`, or None."""
    for i, a in enumerate(args):
        if a in TARGET_FLAGS:
            return args[i + 1] if i + 1 < len(args) else None
        for f in TARGET_FLAGS:
            if a.startswith(f + "="):
                return a.split("=", 1)[1]
    return None


def writes_to_protected(cmd: str, protected):
    """[(path, why)] -- protected paths this command would write.

    Three answers, and the third is the repair:

    * `[(path, why)]` -- a write this can name.  Deny, and say which file.
    * `[]`            -- every segment naming a protected path is a command
                         this knows to be read-only.  Allow.
    * `None`          -- a protected path is named and this cannot say it is
                         only read: unparseable syntax, or a command on neither
                         roll call.  Deny because it cannot be read.

    `None` used to mean the first of those two only, which left every command
    nobody had thought to list -- `python3 -c`, `node -e`, `sudo rm`, `xargs
    rm` -- in the `[]` bucket with `cat`.
    """
    toks = tokenize(cmd)
    mentions = _protected(cmd, protected)
    if toks is None:
        return None if mentions else []
    if any(u in cmd for u in UNSUPPORTED):
        return None if mentions else []

    hits, segs, cur, unreadable = [], [], [], False
    for kind, text in toks:
        if kind == "op" and text in (";", "&&", "||", "|", "&"):
            segs.append(cur)
            cur = []
        else:
            cur.append((kind, text))
    segs.append(cur)

    for seg in segs:
        words = [t for k, t in seg if k == "word"]
        if not words:
            continue
        # Redirection: the word after a > operator is a destination.
        for idx, (kind, text) in enumerate(seg):
            if kind == "op" and ">" in text:
                nxt = next((t for k, t in seg[idx + 1:] if k == "word"), None)
                if nxt and _protected(nxt, protected):
                    hits.append((nxt, f"redirection `{text}`"))
        cmdname = words[0].rsplit("/", 1)[-1]
        args = words[1:]
        # Can this guard say what the command does to a path it is handed?
        # A writer can: its own branch below walks every operand, so "no hit"
        # from it is a finding and not a gap. A reader can. Nothing else can,
        # and an interpreter least of all -- `python3 -c` is handed a program,
        # and no reading of its argv will ever say what that program opens.
        known = cmdname in READERS or cmdname in WRITERS
        # `dd of=<path>` puts the destination in a key=value argument rather
        # than positionally, which is why it needs its own line rather than a
        # row in WRITERS.
        if cmdname == "dd":
            for arg in args:
                if arg.startswith("of=") and _protected(arg[3:], protected):
                    hits.append((arg[3:], "`dd of=` writes it"))
        if cmdname in SUBCOMMAND_WRITERS:
            sub = _subcommand(cmdname, args)
            known = sub in SUBCOMMAND_WRITERS[cmdname] \
                or sub in SUBCOMMAND_READERS.get(cmdname, ()) \
                or cmdname in SUBCOMMAND_DEFAULT_READS
            if sub in SUBCOMMAND_WRITERS[cmdname]:
                for t in args[args.index(sub) + 1:]:
                    if not t.startswith("-") and _protected(t, protected):
                        hits.append((t, f"`{cmdname} {sub}` writes it"))
        if cmdname in WRITERS:
            named = _target_flag(args)
            if named is not None:
                targets = [named]
            else:
                operands = [a for a in args if not a.startswith("-")]
                targets = operands if WRITERS[cmdname] == ALL_ARGS else operands[-1:]
            for t in targets:
                if _protected(t, protected):
                    hits.append((t, f"`{cmdname}` writes it"))
        if cmdname in INPLACE and _in_place(args):
            for t in args:
                if not t.startswith("-") and _protected(t, protected):
                    hits.append((t, f"`{cmdname} -i` edits in place"))
        if not known and any(_protected(w, protected) for w in words):
            unreadable = True
    # `dd if=/dev/zero of=.v4/config.json` reaches both the `of=` branch and
    # the `LAST_ARG` operand, and the same file twice in one refusal reads as
    # two findings.
    hits = list(dict.fromkeys(hits))
    if hits:
        return hits
    return None if unreadable else []
