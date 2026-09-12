# Surface suite integration

Use the adopter's runner and product requirements. Playwright is optional;
installing vibeproof does not install Node, browsers, or invent product tests.
`surface-proof` checks fresh execution evidence. A reviewer still has to decide
whether each assertion proves the requested behavior.

For a web change, the orchestrator first reads existing tests/configuration and
maps each required user journey to a stable case ID. Reuse working tests. When a
journey has no test, assign the existing worker to build it within the authorized
scope, including an isolated local server, disposable data and cleanup. Ask for
missing product acceptance criteria only when the request/code cannot establish
them. Never seed a configured shared/production database to make the test run.
The reviewer reads the requirement and tests independently; check a broken-flow
control, real UI actions and state-owner readback where the flow writes data.
The orchestrator then runs the registered checker and reads its attempt result.
Missing integration, skips and failures leave the claim unproved; maintenance
reports the original claim and routes a bounded repair when authorized. Active
development continues to use its existing task/worktree and maintenance guards.

## Playwright (tested with 1.59.1)

Keep the adopter's own `@playwright/test` dependency. In its original config file:

The host must permit the required loopback connections, local server binding and
browser processes. Codex's supplied V4 profile is offline by default: review a
session/project permission change for the authorized local test using the host's
supported controls. Do not overwrite global permissions or turn a blocked
browser launch into a passing test.

If the host supports approval for a single command, the orchestrator may request
it for the same inspected checker/test invocation. Preserve the denied attempt
and require fresh browser evidence after approval; approval itself is not PASS.
An explicit refusal must not be evaded with another launch tool. Codex's tested
permission flow and session-persistence caveat are described in `docs/CODEX.md`
in the framework checkout.

```ts
import { defineConfig } from '@playwright/test';
import { surfaceConfig } from './.v4/surface/playwright.cjs';
export default defineConfig(surfaceConfig({
  testDir: './tests/e2e',
  use: { baseURL: 'http://127.0.0.1:4173' },
  webServer: { command: 'npm run start:test', url: 'http://127.0.0.1:4173' },
}));
```

Adjust import paths for a nested package; do not relocate its config. Its server
command must use this checkout and isolated data. The helper supports multiple
servers. During proof it forces `reuseExistingServer:false`, zero retries, no
snapshot updates, and stores test artifacts with the private receipt. A port
already in use fails; it does not kill that process. Other configured reporters
must write outside the judged tree or to ignored output paths. Outside a V4 proof
invocation the config is returned unchanged. Remote/prestarted targets need a
separately reviewed context adapter; this local helper does not certify them.

Use the fixture in each required browser case (custom base fixtures are preserved):

```ts
import { test as base, expect } from '@playwright/test';
import { surfaceTest } from '../../.v4/surface/playwright.cjs';
const test = surfaceTest(base);
test('save caption', { annotation: { type: 'v4-case', description: 'caption-save' } },
  async ({ page, request }) => {
    await page.goto('/captions');
    await page.getByLabel('Caption').fill('changed');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByRole('status')).toHaveText('Saved');
    expect(await (await request.get('/api/caption')).json()).toEqual({ caption: 'changed' });
  });
```

Example config fields (use the repo's real command, cwd, IDs and endpoint semantics):

```json
{
  "surface_command": "npm run test:e2e",
  "surface_cwd": ".",
  "surface_kind": "browser",
  "surface_required": ["caption-save"]
}
```

IDs must be unique across selected projects; use distinct annotations per browser
project when several projects are required. The reporter records planned tests
and actual outcomes. All required IDs must pass with browser navigation; skipped,
filtered, API-only, expected-failure and retried cases cannot satisfy them.
`--list` or replacing the reporter cannot produce a valid PASS. Browser presence
alone does not prove the business assertion; the reviewer owns that judgment.

## Closing a finding about browser JavaScript

The same fixture can supply actual Chromium V8 execution evidence to the existing
`review close` red/green route. Use the real browser test as the closing test and
a behavioral mutation or a parent commit; keep its runner available in detached
worktrees, with app/config paths relative to each checkout. Node's V8 coverage
alone cannot observe JavaScript that executes inside a browser.

During that proof, the kernel creates a fresh private coverage directory and
nonce. The fixture records function entry counts, sanitized HTTP asset URLs and
SHA256 of source returned by Chromium. The kernel matches the target's exact
bytes before/after the run and retains `chromium-v8` provenance in checker output.
A different served filename is allowed when the bytes match. This proves those
source bytes executed, not which copy supplied identical bytes. A Python server
child's trace cannot override this browser observation. The mutation must also
load the exact mutated bytes; browser startup/mapping failures do not count as red.

For compiled/minified JS/TS, keep a linked or inline version-3 source map with
`sourcesContent` in the isolated test build. The fixture observes the map URL
through Chromium's debugger, uses Node's `SourceMap` decoder, and matches a
specific V8 function's name segment to a unique original function/arrow name.
The original source hash must match this checkout, including in the red build.
A bundle's module entry or a source filename appearing in a map is insufficient.
Only same-origin HTTP map requests (no redirects) and inline JSON/base64 maps
are read; source filenames never become filesystem reads or further requests.
Evidence retains hashes and coordinates, not source content, data URLs or URL
credentials/query/fragment. Source maps belong in the local test build; this does
not require publishing them with production assets. Real minified TypeScript
acceptance uses esbuild 0.28.2 as a framework test dependency, not an adopter
dependency. Other compilers work only when their actual mappings satisfy this
contract; indexed maps, missing sourcesContent, unlinked maps and unmatched
function shapes remain unproved.

This adapter supports Chromium and the integrated `page` fixture. It does not
observe every popup/worker or promise complete coverage across navigation:
Playwright's `resetOnNavigation:false` is best effort. A missing observation
remains unproved. Skips, expected failures,
retries and snapshot updates do not establish execution proof. A test that calls
the code still needs assertions about its behavior; coverage is not that judgment.
See Playwright's [coverage API](https://playwright.dev/docs/api/class-coverage).
Mapping APIs: [Node SourceMap](https://nodejs.org/api/module.html#class-sourcemap)
and [esbuild source maps](https://esbuild.github.io/api/#sourcemap).

## Failure reporting vocabulary

`fail-closed` recognizes common reporting calls. A direct UI state assignment
may report a failure without calling one of them. When a reviewer has verified
that exact target as a visible failure channel, declare it in the existing
adopter-owned `.v4/fail_closed.json`:

```json
{"vocabulary": {"failure_reporting_assignments": ["view.error.textContent"],
                "failure_reporting_calls": ["http.replyFailure"]}}
```

Use the actual target from the repo; this example is not a default. Entries are
simple dotted assignment targets for JS/TS, not globs, regular expressions or
substrings. Comments, strings, comparisons and other targets cannot satisfy
them. Custom JS/TS response/reporting helpers can be declared as exact bare or
dotted `failure_reporting_calls`; a reference, declaration or similarly named
method does not count as a call. Verify the actual failure status/payload or
visible channel before declaring a helper; its name alone is insufficient.
Both defaults are empty. A declaration does not prove that a message is
truthful or that later code is safe: test the failure UI and state owner,
including failed reads/writes and forbidden follow-on actions where relevant.
Do not declare a channel from its name alone, suppress a real defect, or edit
correct UI behavior solely to imitate a reporting call the checker knows.

Set up this vocabulary before opening development tasks where possible. It
changes the judging rule: widening an existing task to this protected file may
require the normal `scope_widen_protected` authorization. Keep that gate visible;
do not auto-sign, rebase the task or create a replacement task to evade it.

## Other runners and CLI surfaces

`surface_kind` defaults to `command`. The checker injects `V4_SURFACE_RUN_ID`,
`V4_SURFACE_RESULT`, `V4_SURFACE_REPO` and `V4_SURFACE_KIND`. The runner writes JSON
to that fresh result path **after observing real tests**:

```json
{
  "schema": 1,
  "run_id": "value from V4_SURFACE_RUN_ID",
  "kind": "command",
  "cwd": "absolute resolved working directory of the surface command",
  "planned": ["case-id"],
  "checks": [{"id": "case-id", "status": "passed"}],
  "errors": []
}
```

Case statuses are `passed`, `failed`, `skipped`, `not_run`. Planned IDs and results
must match without duplicates; at least one case must execute and pass, no case
may fail or remain unfinished, and each `surface_required` ID must pass. Optional
cases may be explicitly skipped; the receipt retains them. A declared command
that exits zero without valid execution evidence fails the proof (exit 1).
Missing configuration/runner is unsupported (exit 4). Existing output-only
integrations must add a real result adapter; there is no stdout-based fallback.

Browser receipts additionally require nonempty `surface_required`,
`managed_servers:[{"cwd":"absolute directory within repo","reuse":false}]`,
and each required result's `browser:{"run_id":"same run","name":"chromium",
"urls":["http://127.0.0.1:4173/path"]}`. The bundled fixture observes main-page
navigation and omits URL credentials/query/fragment. These fields describe the
runner's observed context, not an independent attestation against a dishonest
runner. A custom adapter needs its own positive and false-green controls.

The existing kernel runner owns timeout, subject/config/program identity,
redaction and ledger attempts. Results live under the Git common directory's
`v4/surface/` (temporary storage for non-Git checker fixtures), outside the source
being judged. For a CLI example see the framework's `tools/surface_probe.sh`.

The installed JS adapters are part of the judging program's hash, even when
`v4 install` updates their ownership manifest. Changing, adding or removing an
adapter makes earlier surface and executable-review evidence stale; installing
new proof code cannot silently preserve a PASS from the old adapter.

Public APIs used: [custom reporters](https://playwright.dev/docs/test-reporters#custom-reporters),
[automatic fixtures](https://playwright.dev/docs/test-fixtures#automatic-fixtures),
[managed web servers](https://playwright.dev/docs/test-webserver).
