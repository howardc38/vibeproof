# Working on vibeproof

The canonical development repository is `howardc38/vibeproof-private`.
Code, tests, documentation, release policy and maintained media sources are
edited there. A checkout containing `.public-release.json` is a published
distribution; its manifest records the source revision.

- Follow the user's requested scope and preserve other work in progress.
- Read implementation and relevant tests before changing factual documentation.
- Keep API/behavior descriptions aligned with code; keep historical measurements
  labelled with their original scope rather than rewriting them as current results.
- Update generated `CLAUDE.md` through `kernel/doctrine.py` or kind-rule data and
  `./bin/v4 --repo . doctrine --write`, not by editing its generated block.
- For a public issue or contribution, preserve the proposed diff and import it
  into canonical development before publishing. Do not silently overwrite a
  public-only hotfix with an export.
- Use `tools/publish_public.py` and the policy in `publishing/public-files.json`
  for releases. Do not mirror private Git history, private operational ledgers,
  risk records or login material into the public repository.
- Run `python3 tools/doc_impact.py --base <base> --head HEAD` to locate likely
  affected documents. Update factual text or record why the changed behavior
  does not alter it; the report does not certify prose semantics.
- Before release, run the affected tests, documentation checks and the public
  distribution checks described in `docs/SYNC.md`. Passing a structural check
  is not proof that every sentence is semantically correct.

Public contributors can work in their public fork and open a PR. The maintainer
imports the contribution into canonical development and credits it in the release.
