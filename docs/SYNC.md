# One canonical source, verified public releases

Develop in **`howardc38/vibeproof-private`**. The public `howardc38/vibeproof`
repository is an exported distribution. Code, tests, README translations,
adoption guides, maintained media sources and release settings have one
editable source in private Git. Public contributors can submit patches through
public PRs; maintainers import them before publishing.

The two repositories intentionally have different Git histories and operational
data. Synchronization means that every published file is attributable to a
specific canonical revision and export policy, not that both directories are
byte-identical in every respect.

## Daily work

1. Make the change in a private branch, including affected documentation/tests.
2. Inspect the implementation before updating behavior claims. Use the source
   map below to find likely affected documents; it is a review aid, not semantic proof.
3. Regenerate `CLAUDE.md` when its generator or kind rules change.
4. Run the relevant tests and `./bin/v4 --repo . accept --here --docs`.
5. Merge the validated source under the repository's normal review policy.

| Code changed | Documentation to inspect |
|---|---|
| CLI/parser | SPEC command/flag sections, USING, REFERENCE, examples and command prompts |
| State, hashing, ledger, risk | SPEC state/ship/risk sections, REFERENCE, FEATURES, README limits |
| Hooks, install, facts | Adoption guides, FACTS, SPEC, agent/monitor instructions |
| Checker/detector analysis | Its described criterion, language limits, fixtures, FEATURES and claims used in media |
| Doctrine or kind rules | Regenerate CLAUDE; inspect the generated text in both framework and adopter contexts |
| Release tools/policy | This file, publishing policy, distribution tests and both CI definitions |

Historical evidence is not rewritten into a claim about new code. Rerun the demo
for current evidence. A recorded video may remain labelled as a demonstration
of its original source version; its existence does not validate a newer checkout.

## What is exported

`publishing/public-files.json` is the executable allowlist. It selects product
code, tests, required registries/configuration, user documentation and public
media. `publishing/repository.json` records the intended About/Topics settings;
these GitHub settings are currently checked/applied separately with `gh`, not
by the file exporter. The public CI file is generated from `publishing/public-config/v4.yml`.

Private Git history, live ledger files, ledger exports, chain heads, risk and
deferral records, migration notes and campaign account/dispatch records are not
exported. Necessary `.v4` registry and configuration files are selected explicitly;
the entire `.v4` directory is neither copied nor discarded.

`.public-release.json` records the source commit, policy/exporter hashes, public
parent and every file's source, content hash and mode. The public verifier checks
the released tree against that manifest. The private publisher additionally
reconstructs the canonical export, so editing a public file and its manifest
together does not silently pass the next release preflight. These are governance
and consistency checks, not an immutable external attestation service.

## Publish from a separate clean public clone

Commit the canonical source first. The target must be a separate clone with the
public origin, not another worktree sharing private Git storage. Keep the target
at public main and preserve its history.

```sh
PUBLIC_HEAD=$(git -C ../vibeproof-public-release rev-parse HEAD)
python3 tools/publish_public.py --source . --target ../vibeproof-public-release \
  --expected-public-head "$PUBLIC_HEAD"
```

The default prints the exact added/changed/removed files and changes nothing.
After reviewing the release scope:

```sh
python3 tools/publish_public.py --source . --target ../vibeproof-public-release \
  --expected-public-head "$PUBLIC_HEAD" --apply --push --pr
```

`--apply` creates a new candidate branch and commit; `--push` publishes that
branch; `--pr` opens the public PR. Nothing force-pushes or directly replaces
public main. Merge using **rebase** after public checks pass, preserving the
single release commit's recorded parent. Do not squash or add a separate merge
commit; the next preflight deliberately rejects an unexplained public history.

If public has a hotfix or other unexpected edit, stop, inspect and import it
into private first. Do not reset/clean the public checkout to make the guard pass.
Only one pending `publish/` PR is allowed; resolve it before proposing another.
A source revision older than or divergent from the last published source is
refused, including a late CI completion. An uncertain push/PR result must be inspected remotely before retrying; the
candidate branch remains available.

## CI and one-time automation setup

Private `v4` CI validates product tests, facts, docs and fixtures. The separate
`Private history audit` workflow retains operational-ledger verification and
reports failures independently. Public CI validates product behavior and the
manifest without requiring private operational records or private history.

At migration, the sealed private export has three legacy engagement rows whose
values were redacted after hashing. Its audit still fails; no rows, hashes or
auditor verdicts were rewritten. This is known historical debt, not a clean
audit. Public publication is gated on product CI and its own provenance; it
does not assert that private operational history is fully verifiable.

`.github/workflows/publish-public.yml` can run after successful private main
push CI, or by manual dispatch on main. It is **disabled until explicitly set up**.
No credential is copied from a developer's local GitHub login into Actions.

Configure in the private repository:

- Variable `PUBLIC_SYNC_APP_ID`: a GitHub App installed only on the target public repo.
- Secret `PUBLIC_SYNC_APP_PRIVATE_KEY`: the App's private key; enter it in GitHub
  Secrets, not in source files or chat.
- Variable `PUBLIC_SYNC_ENABLED=true`: enables the workflow.
- Optional `PUBLIC_SYNC_AUTOMERGE=true`: merge the public PR after successful
  public checks, provided the public base and candidate head have not moved.

The App needs target-repo Contents, Pull requests and Workflows write access,
plus Actions/Checks read access. Its token is short-lived and revoked by the
token action after the job. Private checkout uses the private workflow's own
read token; public PR code does not receive the private App key.

Ordinary `GITHUB_TOKEN` access is repository-scoped and has workflow-trigger
restrictions, which is why cross-repository publication uses a dedicated App.
[GitHub token documentation](https://docs.github.com/en/actions/concepts/security/github_token)

Until App setup is completed, the local publisher works with an already
authorized Git/`gh` login. Do not report that unattended synchronization is
running merely because the workflow file exists.

## Release checks

```sh
python3 -m unittest discover -s tests -p test_public_distribution.py -v
python3 tests/run_without_silent_skips.py
./bin/v4 --repo . accept --here --docs
./bin/v4 --repo . accept --here --fixtures
```

In the generated public candidate, run the same product checks and:

```sh
python3 tools/export_public.py verify --root .
python3 examples/first-proof/run.py --quiet
```

Use a fresh public clone for release validation. A test passing in private
storage can otherwise accidentally depend on history or files that were never
published. Mechanical checks catch missing symbols, command/flag drift,
registry differences and file drift; meaning, promises and whether an example
proves the claimed behavior still need source-based review.
