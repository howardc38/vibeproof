"""Where a table-driven rule keeps its two tables.  One name, both sides.

Some rules here are vocabulary rather than logic: `fail_closed` asks whether a
name is a transport, `secret_patterns` asks which families count as a
credential.  Those tables are data, in a `.json` beside the module, because
`kernel/` is pointed at and never copied (SPEC §8.8) -- so for as long as they
were literals in the source, "add a row rather than a branch" was an
instruction only this repo could follow.

A repo that adopts this framework widens them by writing the **same name**
under `.v4/`.  That is the whole convention, and it is here rather than spelled
out in each module because it is read from two layers:

  * the module, to know which two files to union;
  * `hashing.program_sha`, which walks a checker's imports and has to hash the
    same two -- otherwise an answer outlives the rule that gave it.

It was two names, and the second half went missing exactly the way a
convention-by-coincidence does.  The shipped table is found by
`with_suffix(".json")`, which a walker can reproduce; the adopter's was called
`.v4/fail_closed_vocabulary.json`, which nothing structural could have guessed.
Measured: writing that file moved `program_sha` not at all, and `fail-closed`
is subject-scoped, so the working-tree digest did not cover it either.  A repo
could widen the rule it is judged by -- `union` only ever adds, and a handler
that passed because its call was not recognised as a transport fails once it is
-- and every claim already answered under the narrower table stayed answered.

The shipped table's *existence* is what marks a module as table-driven, which
is why `own` is not simply `.v4/<any module>.json`: `kernel/config.py` would
otherwise claim `.v4/config.json`, a different fact that already has a `config`
field of its own in the staleness key.

Paths only.  Nothing here reads a file or decides anything, so both layers can
import it and neither gains a dependency on the other's rules -- the same
reason `kernel/layout.py` exists, one layer down.  `analysis` may not import
`kernel` (`.v4/layers.json`), and that rule is why this is a file of its own
and not four lines in `layout.py`.
"""

from pathlib import Path

#: Kept here rather than imported from `kernel.layout`: `analysis` may not
#: import `kernel`. The two spellings are checked against each other by test.
DIR = ".v4"


def shipped(module_file) -> Path:
    """The table a module ships: `<module>.json`, beside it."""
    return Path(module_file).with_suffix(".json")


def own(repo_root, module_file) -> Path:
    """Where the repo being judged widens that table: `.v4/<module>.json`."""
    return Path(repo_root) / DIR / (Path(module_file).stem + ".json")
