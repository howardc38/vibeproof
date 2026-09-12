# `.v4/facts.<repo>.json` — the table detectors read

> **One per repo, named after it.** The loader first uses the repo name declared in `.v4/config.json`
> (falling back to the checkout name) to select `.v4/facts.<repo-name>.json`
> and, when that file is absent, falls back to the one other `.v4/facts*.json`
> present — only when there is exactly one; two candidates and no match load
> nothing, and `v4 doctor` says so. The fallback is a
> convenience with teeth: this repo carried only the reference repo's table for
> weeks, so every detector here was reading another project's vocabulary and
> nothing said so. `v4 doctor` reports the fallback as a warning for that reason.

`SPEC.md` §2 says the `external-write` detector finds call sites by AST and
matches each symbol against "registry 嗰張 outbound 清單". This file is that
list, and `kernel/facts.py` is its loader. **No detector may hard-code a repo
symbol.** The kernel loads the table, validates it, and hands it over.

The previous design kept these lists in prose. Four review rounds in a row said
the same thing: the detector cites a table that does not exist. It exists now,
and every row in it carries the `file:line` where the symbol was actually seen.

**A `seen_at` is a call site, not a definition**, and for a while this repo's own
table did not hold to that: four `auth_decision` rows cited the line where the
symbol is *defined*, which `scan_source` deliberately does not count. Nothing
noticed at the time, because nothing ran `verify` -- `doctor` ran `validate`,
which reads the file and not the tree. Both halves are wired now: `doctor` also
checks every `seen_at` against the tree (a gone citation is reported, drift is
printed), and CI runs `./bin/v4 --repo . facts verify --gone-only` on the table
whose `repo` is this one. **`--gone-only` is what makes it runnable**: a citation
is true of a commit, so line numbers move on any commit that adds an import, and
a job that failed on drift is a job nobody wires. Drift prints; gone fails.

---

The adopter-specific counts and provider judgments below are a historical
census of the pinned revision, not a measurement of the current framework or
every adopter. The matching/CLI sections describe current behavior.

## Field by field

Facts for `adopter_a`, derived at commit `b2bd5376128234774a97c6c8ed09718b2cd8d2e4`.
These are the counts of that repository's own table at that commit. The copy
under `tests/fixtures/facts/facts.adopter_a.json` is a reduced fixture for the
tests here, not that table.

| Field | Count | Where it came from |
|---|---|---|
| `outbound_write` | 51 patterns | Read every integration module under `core/integrations/`, `app/chat/`, `core/workers/`, `core/scheduler_policy/`, `core/config/`, plus the psycopg repo layer. |
| `outbound_read` | 26 patterns | Same sweep, taking the read half of each client. |
| `auth_decision` | 16 patterns | Grepped for permission/preauth/tenancy helpers, then read each one to check it actually decides something. |
| `entrypoint_globs` | 18 globs | `core/workers/`, `core/flow_engine/handlers/`, `app/worker_dispatch/`, the two CLIs, the poller, `web/api/routes/`, and the launchd plists in `ops/launchd/`. |
| `ui_globs` | 7 globs | `web/ui/` (React + Vite + Playwright) and `web/landing/` — two separate front ends, not one. |
| `config_files` | 21 files | Everything whose edit changes how something else behaves: deps, lockfile, env definition, build and test config. |
| `protected_paths` | 12 globs | V4's own files plus the V3 framework adopter_a still vendors, plus the two CI workflows. |

### `outbound_write` vs `outbound_read`

`outbound_write` changes the world outside this process. `outbound_read` only
observes it.

The split is not cosmetic. A write claim asks "is there a read-back?" — so the
read-back has to be classified as a read, or answering the claim creates a new
one. The old list said the outbound set was `requests.*`, which contains
`requests.get`, and `core/integrations/blobstore.py:129` is a `requests.head`
read-back added specifically to prove a staged upload was fetchable. Under
`requests.*` that read-back became a write needing its own read-back, forever.

`kernel/facts.py` blocks both halves of that:

* the symbol grammar has no wildcard, so `requests.*` is not a writable
  pattern — `requests.post` and `requests.get` are separate rows;
* `validate()` fails the file if an `outbound_write` **symbol** pattern matches
  an `outbound_read` **symbol** pattern, or if the same pattern string appears on
  both lists in any mode (`_check_write_read_disjoint`).

⚠️ **Regex rows are not compared with each other, and that gap is real.** A write
row `match: regex` whose expression also matches a read row's expression is
accepted -- `_request\(\s*"\w+"` on the write list swallows `_request\(\s*"GET"`
on the read list, which is exactly the loop this invariant exists to stop, in the
one row shape the reference repo actually needs. It cannot be settled here:
`validate()` reads the file, not the tree, and whether two regexes overlap is not
a question about the file. **The catch for that one is the self-trigger fixture**
(SPEC.md §2), which runs the detector on a `before`/`after` pair and fails when
answering a claim raises the same claim again.

### What adopter_a actually uses

Checked in source, not from memory. Negative findings matter as much as the
positive ones:

| Expected | Found |
|---|---|
| `requests` | yes — `core/integrations/feed_graph.py`, `core/integrations/blobstore.py`, `app/chat/client.py` |
| `httpx` | yes — the commerce and LLM providers, and a reference-media search |
| `aiohttp` | **no** — not imported anywhere |
| a chat-platform SDK | **no** — hand-rolled `ChatBotClient` over `requests`, one `_request` transport for every bot API method |
| a social-graph SDK | **no** — hand-rolled `requests.request` against the platform's two graph hosts |
| the first LLM SDK | yes — official: `responses.create/parse/retrieve`, `chat.completions.create/parse`, `images.generate/edit` |
| the second LLM SDK | yes — official: `models.generate_content`, `files.upload/get/delete` |
| a commerce SDK | **no** — raw GraphQL over `httpx.post` to the Admin API |
| SQLAlchemy | **no** — psycopg 3 with raw SQL strings and `psycopg_pool` |
| an object-storage SDK | **no** — `subprocess.run([<cloud CLI>, "storage", "cp"/"rm", ...])` |
| a fourth HTTP path | yes — `http.client.HTTPSConnection` with a pinned IP in `core/integrations/reference_media/downloader.py` (SSRF guard) |

---

## How a pattern is matched

Each row is `{"pattern", "seen_at", "kind"}` plus optional `"match"` and
`"note"`. There are two match modes.

### `"match": "symbol"` (default)

The pattern is a dotted callee name. A source expression matches when its
rendered dotted name equals the pattern, or ends with it on a segment boundary.

```
requests.post     matches  requests.post          and  core.requests.post
.send_message     matches  self.client.send_message
.send_message     does NOT match  x.send_message_with_reply_markup   (segments, not substrings)
.publish          does NOT match  result.publish_id
.publish          does NOT match  the bare local `publish`
```

Symbol matching runs over the AST, which buys four things a grep cannot:

1. **Bare references are candidate sites.** This can recognize callbacks such as
   `asyncio.to_thread(self.client.send_message, ...)`, but seeing a reference is
   not proof that it executes. A `\.send_message\s*\(` regex found 3 sites in adopter_a; the
   AST form finds 19. The 16 it missed were all real chat sends in
   `app/chat/poller/`.
2. **Definitions and imports do not.** `def bootstrap_launch_agent(...)` and
   `from ... import bootstrap_launch_agent` produce no name node, so a pattern
   never matches its own declaration.
3. **Docstrings do not.** `feed_graph.py`'s module docstring describes
   `_request_with_retry(method, path, params)`; prose is not a call site.
4. **Decorators do not collide.** adopter_a has 63 route decorators across all
   verbs at the pinned commit, 34 of them `@router.post(...)`. `requests.post`
   and `http.post` are distinct symbols from `router.post`, so none of them
   match.

Every prefix of a chain is yielded, so `TOOL_PERMISSIONS.get(name)` is also a
reference to `TOOL_PERMISSIONS`. A chain whose base is not a name —
`(dest_folder / filename).write_bytes(content)` — renders as `?.write_bytes`,
so suffix patterns still see it.

**Prefer the suffix form for anything importable under an alias.**
`core/workers/dispatch.py:531` does `import shutil as _shutil`, so
`shutil.copy2` misses it and `.copy2` does not.

### `"match": "regex"`

For evidence that is not a callee name at all. Matched against source lines
with `#` comments blanked and string literals kept, because that is where the
evidence lives:

| Row | Why it cannot be a symbol |
|---|---|
| `\bINSERT\s+INTO\b`, `\bUPDATE …SET\b`, `\bDELETE\s+FROM\b`, `\bSELECT\b` | psycopg takes SQL as a string. `cur.execute` is the same callee for a read and a write, so the verb has to come from the literal. |
| `_request(_with_retry)?\(\s*"POST"` and `^\s*"POST",` | `feed_graph.py` passes the HTTP verb as an argument. The second row exists because four of the seven POSTs are wrapped across lines. |
| `"<cli>"[\s,]*"storage"[\s,]*"cp"` | The upload is a subprocess. `subprocess.run` itself is unusable as a pattern — it also runs ffmpeg, ffprobe and launchctl. |
| `^\s*mutation\s+\w+\s*\(` / `^\s*query\s+\w+\s*\(` | One `http.post` carries every commerce-API operation. The GraphQL operation header is what separates the 14 reads from the 1 write. |
| `\bopen\s*\([^()]*,\s*["'][wxa]…` | The mode is an argument, not part of the name. |
| `\b\w*repo\.(insert\|upsert\|…)` | See "known gaps" below. |

`kind` must be one of the closed set in `kernel/facts.py` (`http`, `db`, `fs`,
`secret`, `system`, `authz`, and `proposed` for a row the installer guessed and
nobody has confirmed yet). The set is small on purpose, `proposed` is also read when reporting unconfirmed rows. The verdict-related
local/remote distinction is `external_write` asking `kind not in LOCAL_KINDS` —
is this a local write, whose failure reports itself, or a remote one, where a
2xx can mean nothing. Do not confuse this vocabulary category with a framework claim kind or an
independent verification of provider semantics.

> Six vendor names used to sit in that set. None was ever read; they were the
> stack of the repo this framework was built against, written into the schema
> of every repo that adopts it — and because the set is closed, an adopter on
> any other vendor had to edit the module to classify its own writes. Anything
> remote is `http`.

---

## Judgement calls, written down

These are the rows where "write or read?" was not obvious. Disagreeing with one
of them is a legitimate edit; not knowing one was made is not.

**`requests.request` → write.** `feed_graph.py:361` takes the verb as a
parameter, so this one call site is every Meta write *and* every Meta read. It
is filed as a write because failing to flag a publish is worse than flagging a
permalink fetch. The GET callers are separately in `outbound_read`, so the
distinction survives at the caller layer.

**`http.post` (the commerce API) → write.** One POST endpoint serves queries and
mutations both. Conservatively a write; the operation-header rows recover the
split.

**LLM inference → write.** `models.generate_content`, `responses.create`,
`chat.completions.create`, `images.generate/edit` can be billable and can create
new results on retry. Their exact persistence and idempotency depend on the
provider and request; do not assume they change no remote state.
That is exactly the `replay` half of the external-write claim. adopter_a
already agrees with itself here — `openai_image.py:328` calls image create/edit
"a non-resumable external write" and sets `max_retries=0` because of it.

**`refresh_long_lived_token` → write.** The transport is `requests.get`. The
effect is rotating a credential. Filed as a write at the caller
(`app/worker_dispatch/error_handling.py:214`); the `requests.get` line inside
`feed_graph.py` stays a read, because that is what the line is.

**`.save` → write, with known imprecision.** PIL's `Image.save` and numpy's
`np.save`. Without it `core/media/caption_render.py`, whose entire job is
rendering a PNG to disk, has no write site at all. It also matches
`core/intake/thumbnail.py:67`, which saves into a `BytesIO` — a false positive
— and `app/chat/poller/poller.py:616`, which is `cursor_store.save`, a real
write filed under the wrong kind.

**`.mkdir()` → excluded on purpose.** 80 sites, all idempotent
`parents=True, exist_ok=True` directory preparation. Including it would have
made directory creation 20% of the write surface. "Wrote somewhere it should
not have" is `protected_paths`' job, not this list's.

**`.commit()` → excluded.** 94 sites, and zero files have a `.commit()` without
also having an `INSERT`/`UPDATE`/`DELETE`. It marks a transaction boundary, not
a write.

**`.fetchone` / `.fetchall` → excluded.** Same reasoning on the read side: they
consume a result the `SELECT` already produced.

---

## Known gaps

Under-reporting is worse than over-reporting, so these are named rather than
quietly tolerated.

1. **DB writes at the caller layer are only partly covered.** The SQL lives in
   `core/integrations/postgres/**`, so a worker that mutates state through
   `self.post_repo.close_meta_submission(...)` shows no SQL of its own. The
   `\b\w*repo\.(insert|upsert|…)` row closes about 100 of those sites, but only
   when the receiver name ends in `repo`. adopter_a also binds repo instances
   to `campaign`, `post`, `run`, `self.runs`, `routes` — those are still
   missed. **The durable fix is a path rule, not a symbol rule:** a detector
   that resolves imports should treat any call into
   `core/integrations/postgres/**` as a DB access. That needs import
   resolution, which is a detector feature, not a facts-file feature.
2. **`Path.replace()`** is the second half of every atomic write here
   (`tmp.write_text(...); tmp.replace(dest)`). There is no pattern for it:
   `.replace` would match every `str.replace` in the repo. Every site is
   flagged anyway by the `write_text` on the line above, so this costs
   granularity, not coverage.
3. **ffmpeg output files.** `core/flow_engine/handlers/video*.py` writes video
   through `subprocess.run(ffmpeg_cmd)`; the output path is an argv element.
   Not patternable without matching every subprocess call in the repo.
4. **The recorded census was Python-only.** The current facts CLI scans
   tracked `.py` and `.go` call sites (`SOURCE_SUFFIXES`). TS/JS structural
   checkers and UI-directory proposal exist, but they do not make the facts
   call-site census a TS/JS or SQL scanner. Those surfaces need explicit review.
5. **`json.dump` to an open handle** (`core/intake/geonames_installer.py:72`)
   is a real file write with no row. The file is flagged by its `.unlink` and
   `.rmtree` sites, so it costs granularity only.

---

## How to verify

These commands run from the framework checkout against the named fixture or
actual adopter table. Python dependencies are stdlib-only; Go extraction needs
the host Go toolchain. Runtime depends on the tree and environment.

```sh
# Every command goes through the launcher, which puts the framework on
# PYTHONPATH. `python3 -m kernel.facts …` does the same only when PYTHONPATH
# already names the framework checkout; in an adopter it fails with
# `No module named 'kernel'`.

# 1. Schema.  Fails loudly; never degrades to an empty table.
./bin/v4 facts validate tests/fixtures/facts/facts.adopter_a.json

# 2. Citations.  Re-checks that every seen_at file:line still matches.
#    Warns first if HEAD is not generated_from_commit or the tree is dirty —
#    verify against a checkout of the pinned commit, or the failures are the
#    checkout's, not the table's.
./bin/v4 facts verify tests/fixtures/facts/facts.adopter_a.json /path/to/adopter_a

# 3. Breadth.  Per-pattern call-site counts across the repo.
./bin/v4 facts scan tests/fixtures/facts/facts.adopter_a.json /path/to/adopter_a outbound_write
./bin/v4 facts scan tests/fixtures/facts/facts.adopter_a.json /path/to/adopter_a outbound_write --sites

# 4. Tests.  The suite's single oracle. The half that reads the reference
#    adopter's table against its checkout runs only where V4_ADOPTER_REPO names
#    one, and the runner says so when it does not.
python3 tests/run_without_silent_skips.py
```

`scan` covers git-tracked `.py` and `.go` source, using its documented test
path/name filters and excluding
`protected_paths`. Tracked-only matters: counting untracked scratch would make
the same table measure differently on two machines. **It is not the same as
"excluding `runtime/`"** -- adopter_a ignores that directory and then
un-ignores four `!runtime/*_proof.py` lines, so those four files are tracked and
do count — 19 `outbound_write` call sites between them. Un-ignoring is a line in
a diff; that is the whole reason tracked-ness is the test rather than the
directory name. Excluding `protected_paths`
matters because adopter_a vendors the whole V3 framework under `auto-dev/`,
whose adoption scripts do plenty of `shutil.rmtree` — that is a different
program's filesystem behaviour.

Measured at `b2bd5376`, excluding tests:

| List | Call sites | Files |
|---|---|---|
| `outbound_write` | 386 | 108 |
| `outbound_read` | 346 | 87 |
| `auth_decision` | 165 | 40 |

`outbound_write` by kind, at the commit above and under the vendor kinds the
table then used: `db` 159, `fs` 130, remote transports 89 in total, `secret` 5,
`system` 3. The shape that matters is the ratio — **289 of the 386 writes are
local**, and it is the other 89 that a `2xx` can lie about.

---

## How to update

**Whenever `generated_from_commit` is older than the repo you are scanning, the
line numbers are suspect.** Run `verify` first; fix what it reports; only then
trust the table.

1. `git -C <repo> rev-parse HEAD` → `generated_from_commit`.
2. Adding a row: find the call in source, read the line, put that `file:line`
   in `seen_at`. **A pattern with no site in the repo does not belong here.**
   This is a facts file, not a wish list — and `verify` will say so.
3. Prefer the suffix form (`.copy2`) over the module-qualified form
   (`shutil.copy2`) for anything that can be imported under an alias.
4. Re-run all four checks above.
5. If `scan` shows a new pattern matching far more than its neighbours, read
   twenty of the sites before keeping it.

### What goes stale first

In rough order, measured rather than guessed:

1. **`seen_at` line numbers.** They rot on any edit to the cited file. During
   the derivation of this table, adopter_a' HEAD moved and four files changed
   inside the same hour, invalidating six citations. This is the field that
   most needs a rebuild step, and `verify` is that step — it should run in CI,
   not by hand.
2. **`auth_decision`.** Two of its members (`require_metadata_preauth`, the
   `Permission` enum) are recent additions with phase tags still in their
   docstrings. A new preauth tier or a new gate helper adds a symbol nobody
   will remember to add here.
3. **Provider symbols.** `responses.create`/`responses.parse` is an OpenAI SDK
   shape that changed once already; `google-genai` is on a 2.x line. A vendor
   rename silently empties a row — which `scan` catches as a zero and `verify`
   catches as a broken citation.
4. **`config_files`.** The one field with no pattern behind it. `web/ui`
   currently has both a `package-lock.json` (untracked) and a `pnpm-lock.yaml`
   (tracked); a package-manager switch would leave the wrong one listed. The
   test `test_every_declared_path_still_exists` is the guard.
5. **`entrypoint_globs` / `ui_globs`.** Directory-shaped, so they survive
   file-level churn. `test_every_glob_still_matches_something` fails when a
   directory is renamed out from under one.
6. **The write/read split itself.** Most stable part of the file. It is a
   judgement about what a protocol verb means, and those judgements do not move
   when the code does.

---

## Optional fields

Three lists a repo may declare that nothing requires. The set is
`OPTIONAL_LISTS` in `kernel/analysis/facts_grammar.py`, and every key in it
belongs in this table -- one that is declarable and undocumented is a
declaration no reader can act on, which is how `route_receivers` sat here
unnamed while `validate` accepted it.

| Field | Read by | What declaring it means |
|---|---|---|
| `public_routes` | `doctor` checks referenced files; no active route-auth verdict | It meant: this handler is deliberately unauthenticated, and the exemption lives here rather than in a docstring because a rule whose exemption is invisible is a rule people route around. The kind that read it was cut at `a9ae5fb`, so the rule it exempted a handler from is not running. |
| `dal_globs` | `kernel/install.py::write_layers` -> `analysis/layers.propose`, and `derive.FILTER_KEYS` | It reads again, and this row said it did not. The kind that read it was cut at `a9ae5fb` for raising no claim in 113 tasks, and the row was written then; `v4 install` later grew a layer draft, and a data layer is the one boundary that cannot be guessed -- the first draft looked for DML literals per directory and made all of `kernel/**` the data layer because `ledger.py` holds the schema. So it is taken from `dal_globs` when the table declares one and left out otherwise. Declaring it today buys a drawn boundary; not declaring it leaves that layer undrafted rather than drafted wrong. |
| `route_receivers` | **nothing live** | It means: what a route decorator hangs off in this repo -- the names in `@app.get(...)`. It is declared in `facts_grammar.OPTIONAL_LISTS` and was described in neither of the two documents that describe this file, which is the gap that put it here. Its one reader is `route_auth.receivers`, called only from `kernel/analysis/webhook_replay.py::scan`, which has no live caller -- `kernel/facts.py` imports that module for `go_handlers`, a different function. So declaring it buys what the two rows above buy, by a longer path: without it `receivers` falls back to eight built-in names, and a repo whose app object is `application` or `admin_api` would find no routes -- if anything asked. |

`public_routes` and `route_receivers` no longer drive the removed authorization
verdict. `dal_globs` has the live layer-drafting use described above. `doctor`
also checks that declared `file::symbol` references name tracked files; a
reference can therefore have diagnostics without being an authorization gate.
`validate` still accepts these optional keys; the cost is on the reader, who was told that
declaring an exemption is what keeps the rule visible and would have been
declaring an exemption from a rule with no program behind it. If your table has
a `public_routes` row, the handler it names is unchecked either way -- by this
framework. `v4 coverage` reports operational-risk class #7 (Authorization guard
tiering) as having no mechanism here, which is where that gap is recorded.
## Empty is a statement; missing is not

`validate` refuses an empty list for most fields, because an empty table makes
every detector report a clean repo. Two are exempt -- `ui_globs` and
`config_files` -- and the reason is worth stating, because the first version got
it wrong in both directions.

**Refusing silence is right for `auth_decision`.** This repo has no HTTP routes,
so the first attempt at its own table declared none. Being made to look again
turned up five: the ledger write gate, the checker fixture gate, the measurement
round freeze, the protected-path decision, and the engagement judgement.

Then the table was written with four of them -- the write gate, the one named
first, was the one left out -- and it stayed that way until a sweep asked. Two
more turned up in the same pass: `kernel/risk.py` refusing to sign when `stdin`
is not a terminal (who may sign), and the hooks emitting
`permissionDecision: deny` (who may write what). That made seven, and on
2026-08-20 a later sweep made it eight: `kernel/analysis/fail_closed.py`'s
`_matches_auth`, the rule that decides what counts as an auth decision at all,
which two checkers ask their question through and which was not in the auth
table. So: 當時 8 auth_decision rows. All eight decide who may do what; the subject
is the kernel rather than a user, which is why they were easy not to see -- and
**the paragraph that named five while the file held four is the same failure one
level out**, which is why `python3 -m kernel.facts verify` is a CI step rather
than a habit.

That paragraph then said seven for the seven days after the eighth row landed,
which is the same failure a third time and the reason the number is now written
the way it is. `python3 -m kernel.facts verify` reads this table against the
code; nothing read it against this document, because `spec_coverage::_reality`
could count checkers, kinds, lenses, detectors, hooks, agents, subcommands and
pins and could not count a facts table. It can now, per table, and
`counted_claims` settles any sentence that names which table it is counting --
which is why that historical example says 當時 `8 auth_decision rows` and not `8 rows`. A bare
number still settles nothing: measured over `docs/`, dropping that requirement
turns 0 findings into 50, of which `v4 check` read as four checks is the
shortest to explain.

The current table has 14 auth_decision rows. They include committed-symbol
rename validation, maintenance repair/dispatch correlation, Telegram recipient
and callback acknowledgement checks, and receiver-service ownership. Telegram
operations are classified by their actual method: identity/webhook inspection
only reads; message delivery, callback UI acknowledgement and update polling
that advances the provider cursor have external effects. Private file writes
use the declared serialization/replacement primitives. Source citations remain
call sites and are verified against the actual implementation.

An eighth arrived the same way, and one had to come back out. `hooks/stop_gate.py`
emits three `decision: block` refusals -- who may end a turn -- while both sibling
hooks were covered by `.protected` and `writes_to_protected` and this one was
named here nowhere; it is `refuse_the_stop` now, and the three refusals go through
that symbol so the row keeps meaning something. What came out was a row naming
`_matches_auth` in `kernel/analysis/fail_closed.py`: a pure classifier returning a
label or `None`, which can neither raise nor refuse, under a path no
`entrypoint_glob` covers. **A table of who may do what is not the place for the
function that decides what a name looks like** -- and the two are one edit,
because a table that names a symbol deciding nothing and misses the surface that
refuses is wrong in both directions at once. So: 8 rows.

**No checker turns `auth_decision` into a verdict any more, and it is still
refused empty.** The kind that read it was cut at `a9ae5fb` (2026-08-24) along
with the analysis that produced its verdict, and the reason the refusal outlived
it is the paragraph above: what the field bought was being made to enumerate who
in this system may do what, and that is worth the same whether or not a program
reads the answer. What is left reading it is `python3 -m kernel.facts verify`,
which fails when a row cites a symbol that has gone, and `v4 doctor`, which
reports rows still marked `proposed`. Both keep the list honest; neither judges a
change. If that trade stops being worth it, the decision belongs in
`kernel/analysis/facts_grammar.py`, which is where the refusal is written.

**Refusing an empty list was not the same thing, and it took a first adoption to
show the difference.** A library whose only outbound call is `requests.get` could
not adopt this framework at all: `outbound_write: []` was refused, and the
refusal asked it for rows that do not exist. The rule was aimed at an adopter
leaving a table blank and getting a silently permissive repo, and it hit a repo
that had looked and found nothing.

So absence stops being silence and becomes a statement, `absent`:

```json
"absent": {
  "outbound_write": "grep -rn 'requests.post|requests.put|open(.*\"w' app/ at 9f2fccb -> 0 hits"
}
```

At least 20 characters, and the named table must actually be empty -- declaring
absence beside three rows is refused as a contradiction. `[]` on its own is still
refused. What changed is that a repo which looked can now say so, in a line with
an author, in a diff, that the next reader can re-run.

`v4 install` writes these too, prefixed `AUTO:`, saying what its scan searched --
a verb-ending sweep of tracked Python/Go, which can miss a write behind `subprocess`
or a client wrapper. `doctor` reports every `AUTO:` row that is still there, and
`v4 ship` refuses while any remain. **The forcing function moved from the gate
that blocked the wrong repo to the one that blocks the wrong ship.**

**Refusing empty is wrong for `ui_globs`.** A repo with no front end says so by
writing `[]`, and the key is required, so `[]` is a statement rather than an
omission. Conflating the two forces a fake glob, which is worse than the gap.

A detector reading an explicitly empty list must treat it as "this surface does
not exist here" and **not** fall back to a generic vocabulary -- falling back
makes a declaration of absence indistinguishable from a table nobody wrote.

## Where a new repo's table comes from

```sh
# `v4 install` writes this file for you. This is how to regenerate it by hand,
# from the repository root. Redirect only a command that has already been seen
# to succeed: a shell `>` truncates the target before the command runs, so a
# failing propose leaves an empty draft behind.
V4_FACTS_DRAFT=$(mktemp)
./bin/v4 facts propose . > "$V4_FACTS_DRAFT" && \
  ./bin/v4 facts validate "$V4_FACTS_DRAFT"
# Inspect the successful output before replacing a reviewed facts file.
# Move it to your chosen .v4/facts.<repo>.json.draft only after validation.
```

Every call site whose dotted name ends in a writing verb, a reading verb or an
auth verb, with the `file:line` where it was found. **Every row is marked
`kind: "proposed"` and every row is a guess** -- `requests.get` reaches outside
and `cache.get` does not, and only somebody who knows the repo can tell them
apart. `doctor` reports how many are still unreviewed, so a table nobody pruned
is visible rather than silent.

It validates as written, but it is not finished: every list the scan found
nothing for carries an `AUTO:` absence line saying what was searched and where.
Those lines are placeholders, not answers. `doctor` reports each one, and `ship`
is held until every one is either confirmed in your own words or replaced with
real rows. `auth_decision` deserves the most suspicion -- a proposal cannot tell
whether this repo decides who may do what, and asked properly of one repo the
answer was five entries nobody had written down.

A write pattern that also matches a read pattern is dropped from the proposal,
because `validate()` refuses that pair and a proposal that cannot be edited into
a valid table is a proposal nobody can use.

## Narrowing diagnostics and their limits

**A facts edit can change what a detector sees; the kernel does not prove that
every narrowing is legitimate or reject every narrowing.** Without
this, pointing `ui_globs` at a directory that does not exist turns a detector off
permanently while the ship report keeps printing that it ran -- the failure the
spec names about changing a suffix set, arriving through the table instead of
through the code, and invisible to the fixture gate because a fixture case brings
its own facts.

**The mechanism is `derive`'s, so it is specified in `SPEC.md` §4** — the empty-glob diagnostic and how a detector is judged to read the table.
For facts-reading detectors, a configured filter glob matching no tracked file
is recorded as narrowed detection and not counted as a healthy run; this also
prevents retracting its claims from that run. It does not itself hold ship.
The older double-run comparison was removed because it rejected legitimate
customization. A smaller but still matching vocabulary can escape this check,
so review the table diff rather than treating the diagnostic as completeness proof.

⚠️ **Which detectors it covers is worth knowing, because it was nearly none.**
"Conditional" used to be decided by scanning the detector's own text for
`facts.get(`, and of the three detectors that consumed the table **at the time**
only one read it in its own file — the other two reached it through
`kernel/analysis/`. So the guard against a table silently disabling a detector
was watching one of the three it exists for. It follows imports now. (Two of
those three, and the one that read the table directly, went at `a9ae5fb`; the
detectors that consume the table today are `external_write.py`,
`surface_proof.py` and `runtime_proof.py`, and `tests/test_kernel.py` pins that
set.)

`v4 facts <subcommand> --help` and `-h` are handled before repository/table lookup
or command dispatch. Asking for help cannot invoke `restate` or change citations;
the same rule applies to `python3 -m kernel.facts` before an explicit `--`.

With explicit arguments, `verify`, `restate` and `scan` require both the facts
table and repository root. Flags may appear between positional arguments; a
flag never supplies a missing root. Missing/extra arguments, unknown options
and unknown scan tables return usage error (exit 2) before any `restate` write.
`v4 --repo <root> facts verify --gone-only` still fills in that repo's paths.
