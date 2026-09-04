"""Pure analysis for the ``secret`` claim kind.  SPEC.md §3.

This module is the one place the rule lives.  ``checkers/secret_scan.py`` is a
thin argv/exit-code wrapper around it.

**No argv, no exit codes, no output.**  No ``print``, no ``sys.exit``, nothing
read off the command line.  Callers hand in source text and get back a report.

**It does read two files.**  The shipped ``secret_patterns.json`` beside this
module, at import; and the adopter's own ``.v4/secret_patterns.json`` through
:func:`table_for`.  This paragraph used to say "No I/O here.  No ``open``…",
which was never true here either -- the same sentence in
``kernel/analysis/fail_closed.py`` was false for the same reason and for the
same table.  SPEC §12 has the count: 10 of this layer's 22 modules read files,
3 run git, and no mechanism checks the claim.  What the layer really promises
is *the judgement lives here; argv and exit codes do not*.

--------------------------------------------------------------------------------
What this module is for
--------------------------------------------------------------------------------

V3's ``SECURITY_GATE/tooling/scanners/secret-scan.js`` matched a table of vendor
prefixes and reported every hit as P0.  Run against ``adopter_a`` on 2026-08-06
that produced **78 P0 findings, not one of which was a credential this repo
committed** -- 73 sat in ``.venv/`` and were vendor documentation, third-party
test data or published example ids; the 5 first-party ones were CI
service-container defaults, a test fixture URL, and two scan reports quoting the
fake token they had asked someone to substitute in.

In V3 nothing consumed that exit code, so the noise was free.  Under V4 a
checker's exit code *is* the verdict, so a scanner that is wrong every time it
speaks does not protect anything: it blocks the first task that runs it and then
gets switched off.

So this module keeps V3's matching (nothing that V3 found is lost) and adds the
half V3 never had: **a classifier that decides, mechanically and per-finding,
whether the matched text is a credential or a fixture.**  Every suppression
carries a rule id and appears in the checker's ``--explain`` output, so what was
silenced is auditable rather than invisible.

--------------------------------------------------------------------------------
The classifier, and where each rule came from
--------------------------------------------------------------------------------

The rules below were *derived from* the 78 findings, not invented.  Each rule
names the findings that motivated it.  The count in brackets is how many of the
78 that rule alone accounts for.

  ``template-placeholder``  [16]  The credential is not a literal at all: it
        contains a substitution, redaction or angle/brace/bracket construct.
        From ``<key bytes>``, ``<a very long private key string>``,
        ``{access_key}:{secret_key}``, ``{}:{}``, ``***:***``,
        ``<username>:<password>``, ``[user:passwd@``, ``[ref@``, and the
        minified-JS mis-parse in ``playwright/.../utilsBundleImpl/index.js``.

  ``placeholder-word``      [29]  Every alphabetic token in the credential is a
        placeholder noun.  From ``username:password``, ``user:password``,
        ``myuser:mypassword``, ``userid:password``, ``me:secret``,
        ``username:pwd``, ``username:token``, ``operator:secret-password``,
        ``org:repo``, ``samuel:pass``, ``REDACTED:REDACTED``, ``xxx:xxx``,
        ``postgres:postgres``.

  ``user-equals-password``  [9]   URI userinfo where user == password.  No
        credential system issues a password identical to the username.  From
        ``postgres:postgres`` (CI service container), ``xxx:xxx``,
        ``REDACTED:REDACTED``.

  ``declared-fake-marker``  [15]  The token body carries a word that exists to
        say "this is not real".  From AWS's own published example ids
        ``AKIAIOSFODNN7EXAMPLE`` / ``AKIAI44QH8DHBEXAMPLE`` /
        ``AKIA111111111EXAMPLE``, and ``shpat_TEST1234567890abcdef...``.
        This rule also subsumes V3's separate ``fake_prefix`` allowlist --
        every prefix in ``scripts/data/secret-prefixes.json``
        (``sk-test-``, ``shpat_test_``, ``AKIATEST``, ``AIzaTest``,
        ``github_pat_test_``, ``xoxb-test-``) contains ``test``.

  ``degenerate-body``       [3]   The token body carries no information: a run
        of >=6 identical characters, a monotone run of >=6
        (``1234567890``, ``abcdef``), or <=4 distinct characters.  From
        ``ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx``, ``AKIA111111111EXAMPLE``,
        ``AKIA1234567890ABCDEF``.

  ``pem-without-key-material`` [5]  A ``BEGIN/END PRIVATE KEY`` pair with no
        base64 payload between them -- a marker constant or a doc snippet, not
        a key.  From ``cryptography/.../ssh.py`` (``_SK_START``/``_SK_END``),
        ``google/auth/crypt/_python_rsa.py`` (``_PKCS1_MARKER``), and the
        ``"-----BEGIN EC PRIVATE KEY-----\\n<key bytes>\\n..."`` docstring.

  ``pragma-waived``         [0]   Carried over from V3's ``lineScopedWaivers``
        so an explicit ``# pragma: allow-secret`` keeps working -- but reported
        as a suppression rather than applied as a blindfold.  V3 blanked the
        waived line before matching, so a waived value left no trace anywhere.
        Here the match still happens and ``--explain`` names the waiver.

**Deliberately NOT a rule: "the path is under tests/".**  A real key
fat-fingered into a test file is exactly what a secret scanner exists to catch,
and adopter_a has already had one (``runtime/gitleaks-review-redacted.json``
records "replace real Shopify token with fake in test fixture --
tests/integrations/mcp/test_bridge.py:30 contained the user-provided real
token").  Every rule above reads the *value*, never the directory.

**Deliberately NOT a rule: Shannon entropy.**  It was tried and is not needed --
the three structural tests in ``degenerate-body`` cover every low-information
body in the corpus.  An entropy floor is the rule most likely to swallow a
genuine short key (an AWS body is only 16 characters), so it is not shipped.

--------------------------------------------------------------------------------
When the classifier is wrong -- measured, not guessed
--------------------------------------------------------------------------------

Every one of these was run against this module; the rule named is the one that
actually fired.  A rule that silences a real credential is a *miss*, and misses
are the price of a 0% false-positive rate.

  ``placeholder-word``  misses a real password that is a dictionary word.
        ``postgresql://appuser:password@db.prod.internal/app`` is silenced.
        Mitigation: none here.  A password of ``password`` is a finding for a
        weak-credential rule, not for a leaked-credential rule.

  ``user-equals-password``  misses ``appuser:appuser@db.prod.internal``.
        Same shape, same answer.

  ``declared-fake-marker``  misses a real key whose random body happens to
        contain a marker: ``AKIATESTQRXZMNBVCPLK`` is silenced.  For a random
        16-character AWS body the chance of a spurious ``test`` is about 1e-5;
        for a 36-character alphanumeric body, about 4e-5.  This is why the
        marker list is short and why ``1234567890`` was taken out of it.

  ``template-placeholder``  misses a real URI password containing a brace:
        ``postgresql://svc:aX9{qw}Lm2Zp7Rt4Vb@db.prod/app`` is silenced.  This
        is the narrowest real miss and the one worth revisiting first -- a
        one-brace password is plausible where ``{access_key}`` is not.

  ``is_vendored``  misses a real credential committed under ``vendor/`` or a
        checked-in ``node_modules/``.  Deliberate: see below.

**The larger gap is not the classifier -- it is the pattern table.**  These 11
patterns come from V3 and cover 11 credential shapes.  adopter_a' own
credential inventory (``core/config/provider_secrets.py``, ``.env.local``,
``core/observability/logging.py``) contains at least four families this table
does not match at all, so they produce no candidate for the classifier to rule
on:

  ============================================  ==========================
  social graph long-lived token ``EAA...``      no pattern
  chat-platform bot token ``12345678:AA...``    no pattern
  the same platform, 32-hex ``api_hash``        no pattern
  Stripe webhook secret ``whsec_...``           no pattern
  generic 64-hex HMAC signing key               no pattern
  ============================================  ==========================

Cleaning up the false positives made this checker usable.  It did not make it
comprehensive, and adding those rows is the next unit of real work.

--------------------------------------------------------------------------------
Scope, which is not suppression
--------------------------------------------------------------------------------

73 of the 78 sat under ``.venv/lib/python3.12/site-packages/``.  A credential
inside a pip-installed package is not something this repo committed and not
something this repo can fix, so ``is_vendored`` marks those paths out of scope.
That is a *scope* decision, reported as such, and it is load-bearing exactly
once: ``.venv/.../tornado/test/test.key`` is a structurally genuine PEM with a
real base64 payload, and no content rule can or should tell it apart from a
leaked key.  With vendored paths in scope the content rules alone still take the
78 down to that single file.
"""

from __future__ import annotations

import base64
import binascii
import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import tables

# ==============================================================================
# PATTERN REGISTRY -- ported 1:1 from the V3 single source of truth,
# auto-dev-framework/scripts/data/secret-prefixes.json (version 1).
# Adding a vendor is a row, not a branch.
# ==============================================================================

KIND_PROVIDER = "provider"
KIND_PRIVATE_KEY = "private-key"
KIND_URI = "uri"


@dataclass(frozen=True)
class Pattern:
    """One credential shape.

    ``order`` is significant and matches V3: a more specific prefix must be
    tried before a less specific one, or ``sk-ant-...`` reports as an OpenAI
    key and ``github_pat_...`` as a classic GitHub PAT.
    """

    id: str
    label: str
    kind: str
    regex: re.Pattern[str]
    order: int
    prefix: str = ""          # stripped off before the body is judged


def _p(id_: str, label: str, kind: str, source: str, order: int, prefix: str = "") -> Pattern:
    return Pattern(id=id_, label=label, kind=kind, regex=re.compile(source), order=order, prefix=prefix)


#: The shipped table, loaded from the JSON beside this module.
#:
#: It used to be a literal here, which made a credential family a code change:
#: an adopter measured here had four this table does not match (a social graph
#: token, a chat-platform bot token, a 32-hex api hash, generic hex HMAC), and
#: adding them meant editing the kernel. A regex table is data. ``structural_lint``
#: did not follow, because its three rules are AST walkers -- code, not data
#: wearing code's clothes.
#:
#: Reading its own table at import is not I/O on the subject: nothing here
#: opens, prints, or exits on what it is asked to judge, which is what the rule
#: at the top of this file is about.
_TABLE = json.loads(
    tables.shipped(__file__).read_text(encoding="utf-8"))


def _rows(rows) -> tuple:
    return tuple(_p(r["id"], r["label"], r["kind"], r["regex"], r["order"],
                    r.get("prefix", "")) for r in rows)


PATTERNS: tuple[Pattern, ...] = _rows(_TABLE["patterns"])


def extended(rows, table=None) -> tuple:
    """The shipped families plus an adopter's own.  SPEC.md §8.

    Union, never replace -- the same decision as ``protected_paths``, for the
    same reason: a repo that declares four of its own must not thereby lose the
    twelve it did not have to think about. Ids already present are ignored, so
    an adopter cannot silently shadow a shipped rule.

    Returns a table rather than mutating `PATTERNS`, which is what it used to
    do. Mutating meant `analyse_source` answered differently depending on
    whether some earlier caller in the same process had called this -- and the
    two production callers are different processes. `checkers/secret_scan.py`
    called it; `kernel/engagement.py` did not, while asserting in its own
    docstring that it judges "by the same classifier `checkers/secret_scan.py`
    uses". So an adopter-declared family passed the engagement gate and then
    made `.v4/ledger_export.jsonl` uncommittable, hours later, which is the
    exact failure the refusal text at `judge_text` warns about.

    `external_write.analyse_source` takes its table as a parameter for the same
    reason: one rule, one input, no hidden state.
    """
    base = tuple(table if table is not None else PATTERNS)
    known = {p.id for p in base}
    return base + tuple(_rows(r for r in rows if r.get("id") not in known))


def table_for(repo_root) -> tuple:
    """The pattern table this repo is judged by.  Raises on one it cannot read.

    One place that answers "which families count here", so the checker and the
    engagement gate cannot be looking at different sets. A table that does not
    parse is an error, never an empty one: reading it as empty disarms the scan
    that the file exists to widen.
    """
    import json as _json
    own = tables.own(repo_root, __file__)
    if not own.is_file():
        return PATTERNS
    return extended(_json.loads(own.read_text(encoding="utf-8")).get("patterns", []))

#: Base64 wrapper scan, ported from V3's ``encoded_scan``.
ENCODED_MIN_CHARS = 24
ENCODED_MAX_CHARS = 8192
_ENCODED_RE = re.compile(
    rf"(?:^|[^A-Za-z0-9+/])([A-Za-z0-9+/]{{{ENCODED_MIN_CHARS},{ENCODED_MAX_CHARS}}}={{0,2}})(?=$|[^A-Za-z0-9+/=])"
)

#: V3's ``pragma_markers``.  The pragma line and the line after it are blanked.
_PRAGMA_RE = re.compile(
    r"^\s*(?:#\s*pragma:\s*allow-secret"
    r"|//\s*pragma:\s*allow-secret"
    r"|<!--\s*pragma:\s*allow-secret\s*-->)\s*$",
    re.IGNORECASE,
)

# ==============================================================================
# CLASSIFIER TABLES -- derived from the 78 V3 findings on adopter_a.
# Widening or narrowing the classifier is a table edit, not a code edit.
# ==============================================================================

RULE_TEMPLATE = "template-placeholder"
RULE_PLACEHOLDER_WORD = "placeholder-word"
RULE_USER_EQ_PASSWORD = "user-equals-password"
RULE_FAKE_MARKER = "declared-fake-marker"
RULE_DEGENERATE = "degenerate-body"
RULE_PEM_EMPTY = "pem-without-key-material"
RULE_PRAGMA = "pragma-waived"
RULE_SECRET = "secret"          # the verdict when nothing suppresses

#: Characters that cannot occur in a literal credential but do occur in a
#: template, a redaction or a mis-parse.  ``,`` and ``;`` are NOT here: both are
#: legal URI sub-delims and a real password may contain them.
#:
#: That test was stated for two characters and not applied to five others that
#: pass it. RFC 3986 sub-delims are ``! $ & ' ( ) * + , ; =``, and ``$``, ``(``,
#: ``)``, ``*`` and ``'`` were all in this set -- so a ``postgresql://`` URI
#: whose password is built out of them was suppressed as a template. ``$`` is
#: the character a generated password is most likely to carry: a bcrypt hash
#: starts ``$2b$``.
#:
#: **The URI itself lives in** ``tests/fixtures/secret/red/``
#: ``db_url_with_sub_delims.md``, **and not in this comment.**  It was written
#: out here, and from the moment those five characters left the set above, this
#: file tripped its own rule: ``checkers/secret_scan.py`` against
#: ``kernel/analysis/secret_patterns.py`` alone exited 1 and named line 304 as a
#: committed credential. The file an adopter must edit to add a credential
#: family could not be touched without carrying a ``secret`` claim nobody can
#: answer, and the pragma this module implements was not on the line either. A
#: literal that has to look real belongs in a fixture, which is where the rule
#: it demonstrates is exercised anyway.
#:
#: What those five were doing here is real, and it is a *shape* rather than a
#: character -- ``${VAR}``, ``$(cmd)``, ``%(name)s``. Those moved to
#: ``TEMPLATE_FORMS`` below, where they suppress the template without
#: suppressing every password that happens to contain a dollar sign.
TEMPLATE_CHARS: frozenset[str] = frozenset('<>{}[]"`\\ \t\r\n…')

#: Template *shapes*: a substitution, not a character that appears in one.
TEMPLATE_FORMS: tuple = (
    re.compile(r"\$\{[^}]*\}"),        # ${DB_PASSWORD}
    re.compile(r"\$\([^)]*\)"),        # $(pass show db)
    re.compile(r"%\([A-Za-z_]\w*\)s"),  # %(password)s
    re.compile(r"%[sdr]\b"),           # %s
    re.compile(r"\{\{[^}]*\}\}"),      # {{ password }}
)

#: Alphabetic tokens that name a slot rather than fill one.  Every entry is
#: attested in the 78 findings or in adopter_a' own fixture vocabulary
#: (``shpat_test_supersecret_value_xyz123fake``,
#: ``AIza_test_redact_fixture_...``, ``sk-live-not-a-real-secret``).
PLACEHOLDER_WORDS: frozenset[str] = frozenset({
    # credential nouns -- the slot name used in place of a value
    "password", "passwd", "pass", "pwd", "secret", "secrets", "token", "key",
    "apikey", "credential", "credentials", "auth", "authtoken", "accesskey",
    "secretkey", "privatekey", "mypassword", "mysecret", "mytoken",
    "supersecret", "yourpassword", "yourkey", "yoursecret",
    # identity nouns
    "user", "username", "userid", "uname", "login", "me", "my", "your", "our",
    "admin", "root", "guest", "anonymous", "operator", "owner", "someone",
    # redaction / substitution markers
    "redacted", "redact", "hidden", "masked", "elided", "omitted", "sanitized",
    # explicitly-fake markers
    "example", "sample", "test", "testing", "fake", "dummy", "mock", "stub",
    "fixture", "placeholder", "changeme", "notreal", "invalid", "unused",
    "none", "null", "nil", "todo", "tbd", "demo", "sandbox", "local",
    # metasyntactic
    "foo", "bar", "baz", "qux", "quux", "spam", "eggs",
    "xxx", "xxxx", "yyy", "zzz", "abc", "asdf",
    # infrastructure nouns that appear where a value belongs
    "postgres", "postgresql", "mysql", "mariadb", "redis", "mongo", "mongodb",
    "localhost", "host", "hostname", "server", "db", "database",
    "org", "repo", "workgroup", "domain", "bucket", "project", "app",
})

#: Words that mark a token body as declared-fake.  Substring match, case
#: insensitive.  Kept short on purpose: every extra entry is a chance to
#: swallow a real key whose random body happens to contain it.  For a 36
#: character alphanumeric body the chance of a spurious ``test`` is about
#: 4e-5; of ``example`` about 1e-11.
#: ``1234567890`` deliberately is NOT here: it is degenerate, not *declared*
#: fake, and ``degenerate-body`` already catches it with the truer reason.
FAKE_MARKERS: tuple[str, ...] = tuple(_TABLE["fake_markers"])

#: Path segments whose contents this repo did not author and cannot fix.
VENDORED_SEGMENTS: frozenset[str] = frozenset(_TABLE["vendored_segments"])

DEGENERATE_IDENTICAL_RUN = 6
DEGENERATE_MONOTONE_RUN = 6
DEGENERATE_MAX_DISTINCT = 4

#: A PEM block is a key only if it carries this much contiguous base64.
PEM_MIN_PAYLOAD = 40
_PEM_BODY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(.*?)-----END", re.DOTALL)
_PEM_HEADER_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/=]+")


# ==============================================================================
# Findings
# ==============================================================================

@dataclass(frozen=True, order=True)
class Candidate:
    """One raw pattern match, before the classifier has ruled on it."""

    path: str          # repo-relative
    line: int
    col: int
    pattern_id: str
    label: str
    kind: str
    text: str          # the matched text, verbatim
    line_text: str     # the whole source line, for the human reading stdout

    @property
    def excerpt(self) -> str:
        """The match verbatim, shortened so stdout stays readable for PEM blocks.

        Only ever used for a match the classifier has already ruled a fixture.
        """
        return self.text if len(self.text) <= 72 else self.text[:69] + "..."

    @property
    def redacted(self) -> str:
        """Enough of the match to find and identify it, without the secret.

        A checker's stdout goes verbatim into the ledger, and the ledger is
        durable.  Printing a live credential there leaks it a second time, into
        a store that is harder to rotate than the file it came from.  So a
        *finding* is shown as its recognisable head plus a length; the file and
        line are what the reader needs anyway.  A *suppressed* match is printed
        in full, because the classifier has just finished establishing that it
        is not a credential.
        """
        if self.kind == KIND_PRIVATE_KEY:
            # Take the header by pattern, never by splitting on a newline: a PEM
            # embedded in a JSON string has its newlines escaped, so a split
            # would hand back the entire key.
            header = _PEM_HEADER_RE.search(self.text)
            head = header.group(0) if header else "-----BEGIN PRIVATE KEY-----"
            return f"{head} [redacted, {len(self.text)} chars]"
        if self.kind == KIND_URI:
            user, password = uri_userinfo(self.text)
            scheme = self.text.split("://", 1)[0]
            return f"{scheme}://{user}:[redacted, {len(password)} chars]@"
        pattern = PATTERN_BY_ID.get(self.pattern_id.removeprefix("encoded_"))
        prefix = pattern.prefix if pattern else ""
        shown = self.text[: len(prefix) + 4] if prefix else self.text[:6]
        return f"{shown}[redacted, {len(self.text)} chars total]"


@dataclass(frozen=True, order=True)
class Judgement:
    """Why a candidate is, or is not, a credential."""

    is_secret: bool
    rule: str
    why: str


@dataclass(frozen=True, order=True)
class Verdict:
    """A candidate joined to its judgement.  Ordered so output is deterministic."""

    candidate: Candidate
    judgement: Judgement


@dataclass(frozen=True)
class Report:
    """Everything one file's text yielded."""

    findings: tuple[Verdict, ...] = field(default_factory=tuple)
    suppressed: tuple[Verdict, ...] = field(default_factory=tuple)


# ==============================================================================
# Body extraction -- what part of a match is supposed to be high-entropy
# ==============================================================================

def token_body(text: str, pattern: Pattern) -> str:
    """The part of a provider token after its vendor prefix."""
    if pattern.id == "slack":                       # xoxb- / xoxp- / ...
        return text.split("-", 1)[1] if "-" in text else text
    if pattern.prefix and text.startswith(pattern.prefix):
        return text[len(pattern.prefix):]
    return text


def uri_userinfo(text: str) -> tuple[str, str]:
    """``scheme://user:password@`` -> ``(user, password)``.

    Split at the first ``:`` after ``://``, because the pattern's user class
    already excludes ``:``.  A trailing ``@`` is dropped.
    """
    after = text.split("://", 1)[1] if "://" in text else text
    userinfo = after[:-1] if after.endswith("@") else after
    user, _, password = userinfo.partition(":")
    return user, password


def pem_payload(text: str) -> str:
    """The longest contiguous base64 run between BEGIN and END."""
    match = _PEM_BODY_RE.search(text)
    if match is None:
        return ""
    runs = _BASE64_RUN_RE.findall(match.group(1))
    return max(runs, key=len) if runs else ""


# ==============================================================================
# Rule primitives
# ==============================================================================

_PERCENT_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def percent_decoded(value: str) -> str:
    """``a%20secret`` -> ``a secret``.  Unrecognised escapes are left alone."""
    return _PERCENT_RE.sub(lambda m: chr(int(m.group(1), 16)), value)


def has_template_char(value: str) -> str:
    """The first template/redaction character in ``value``, or "".

    A whole substitution counts too, and is reported as itself -- ``'${DB_PW}'``
    reads better in a finding than ``'$'``, and it is the difference between
    suppressing a template and suppressing every password with a dollar in it.
    """
    decoded = percent_decoded(value)
    for form in TEMPLATE_FORMS:
        m = form.search(decoded)
        if m:
            return m.group(0)
    for ch in decoded:
        if ch in TEMPLATE_CHARS:
            return ch
    return ""


def alpha_tokens(value: str) -> list[str]:
    """Alphabetic runs, lowercased: ``secret-password`` -> ``[secret, password]``."""
    return [t.lower() for t in re.findall(r"[A-Za-z]+", value)]


def all_placeholder_words(value: str) -> bool:
    """True when every alphabetic token names a slot instead of filling one."""
    tokens = alpha_tokens(value)
    return bool(tokens) and all(t in PLACEHOLDER_WORDS for t in tokens)


def fake_marker(value: str) -> str:
    """The first declared-fake marker in ``value``, or ""."""
    low = value.lower()
    for marker in FAKE_MARKERS:
        if marker in low:
            return marker
    return ""


def longest_identical_run(value: str) -> int:
    best = run = 0
    previous = ""
    for ch in value:
        run = run + 1 if ch == previous else 1
        previous = ch
        best = max(best, run)
    return best


def longest_monotone_run(value: str) -> int:
    """Longest run of consecutive code points, ascending or descending."""
    best = run = 0
    step = 0
    for i, ch in enumerate(value):
        if i == 0:
            run = 1
        else:
            delta = ord(ch) - ord(value[i - 1])
            if delta in (1, -1) and (step == 0 or delta == step):
                run += 1
                step = delta
            else:
                run = 2 if delta in (1, -1) else 1
                step = delta if delta in (1, -1) else 0
        best = max(best, run)
    return best


def degenerate_reason(body: str) -> str:
    """Why this token body carries no information, or ""."""
    identical = longest_identical_run(body)
    if identical >= DEGENERATE_IDENTICAL_RUN:
        return f"a run of {identical} identical characters"
    monotone = longest_monotone_run(body)
    if monotone >= DEGENERATE_MONOTONE_RUN:
        return f"a monotone run of {monotone} characters"
    distinct = len(set(body))
    if body and distinct <= DEGENERATE_MAX_DISTINCT:
        return f"only {distinct} distinct characters"
    return ""


def is_vendored(rel_path: str) -> str:
    """The path segment that puts this file outside the repo's authorship, or ""."""
    for segment in rel_path.replace("\\", "/").split("/"):
        if segment in VENDORED_SEGMENTS:
            return segment
    return ""


# ==============================================================================
# The classifier
# ==============================================================================

def judge(candidate: Candidate, table=None) -> Judgement:
    """Is this match a credential?  One rule fires; its id is the audit trail."""
    if candidate.kind == KIND_URI:
        return _judge_uri(candidate, table)
    if candidate.kind == KIND_PRIVATE_KEY:
        return _judge_private_key(candidate)
    return _judge_token(candidate, table)


def _judge_uri(candidate: Candidate, table=None) -> Judgement:
    user, password = uri_userinfo(candidate.text)
    ch = has_template_char(password) or has_template_char(user)
    if ch:
        return Judgement(False, RULE_TEMPLATE,
                         f"userinfo contains {ch!r}, so it is a template or a "
                         f"mis-parse, not a literal credential")
    if user and password and user.lower() == password.lower():
        return Judgement(False, RULE_USER_EQ_PASSWORD,
                         f"user and password are both {user!r}")
    if all_placeholder_words(password):
        return Judgement(False, RULE_PLACEHOLDER_WORD,
                         f"password {password!r} is made only of placeholder words")
    return Judgement(True, RULE_SECRET,
                     f"URI carries a literal password for user {user!r}")


def _judge_private_key(candidate: Candidate) -> Judgement:
    payload = pem_payload(candidate.text)
    if len(payload) < PEM_MIN_PAYLOAD:
        return Judgement(False, RULE_PEM_EMPTY,
                         f"BEGIN/END pair encloses {len(payload)} base64 characters "
                         f"(a key needs at least {PEM_MIN_PAYLOAD}), so this is a "
                         f"marker constant or a documentation snippet")
    return Judgement(True, RULE_SECRET,
                     f"BEGIN/END pair encloses real base64 (longest unbroken run "
                     f"{len(payload)} characters), so there is key material here")


def _judge_token(candidate: Candidate, table=None) -> Judgement:
    # The table this scan is running with, not the module global. `extend`
    # mutated that global, so which patterns existed depended on whether an
    # earlier caller in the process had called it.
    by_id = {x.id: x for x in table} if table else PATTERN_BY_ID
    pattern = by_id[candidate.pattern_id.removeprefix("encoded_")]
    body = token_body(candidate.text, pattern)
    ch = has_template_char(body)
    if ch:
        return Judgement(False, RULE_TEMPLATE,
                         f"token body contains {ch!r}, so it is a template")
    marker = fake_marker(body)
    if marker:
        return Judgement(False, RULE_FAKE_MARKER,
                         f"token body declares itself fake with {marker!r}")
    reason = degenerate_reason(body)
    if reason:
        return Judgement(False, RULE_DEGENERATE,
                         f"token body carries no information: {reason}")
    return Judgement(True, RULE_SECRET,
                     f"token body has the shape of a live {candidate.label}")


PATTERN_BY_ID: dict[str, Pattern] = {p.id: p for p in PATTERNS}


# ==============================================================================
# Matching
# ==============================================================================

def waived_lines(text: str) -> frozenset[int]:
    """1-based line numbers covered by an explicit ``pragma: allow-secret``.

    V3's ``maskLineScopedSecretWaivers`` blanked those lines *before* matching,
    so a waived value produced no match and left no trace -- a blindfold, not a
    record.  Here the match still happens and the waiver becomes a suppression
    with its own rule id, so ``--explain`` can show an operator exactly what
    their pragma is holding back.  The scope is unchanged: the pragma line
    itself, plus the one line after it.
    """
    out: set[int] = set()
    waive_next = False
    for number, line in enumerate(text.split("\n"), start=1):
        if _PRAGMA_RE.match(line):
            out.add(number)
            waive_next = True
            continue
        if waive_next:
            out.add(number)
            waive_next = False
    return frozenset(out)


def _line_col(text: str, index: int) -> tuple[int, int, str]:
    before = text.count("\n", 0, index)
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    line_text = text[start:] if end == -1 else text[start:end]
    return before + 1, index - start, line_text


def _direct_matches(text: str, path: str, offset: int = 0, source: str | None = None,
                    table=None):
    """Every pattern match in ``text``, more-specific patterns first.

    A match whose span is contained in an already-accepted match is dropped:
    ``sk-ant-...`` satisfies the OpenAI shape too, and reporting one leaked key
    twice with two vendor labels is noise of exactly the kind this module exists
    to remove.  (V3 reported both.)
    """
    located = source if source is not None else text
    taken: list[tuple[int, int]] = []
    out: list[Candidate] = []
    for pattern in sorted(table or PATTERNS, key=lambda p: p.order):
        for match in pattern.regex.finditer(text):
            span = (match.start(), match.end())
            if any(a <= span[0] and span[1] <= b for a, b in taken):
                continue
            taken.append(span)
            line, col, line_text = _line_col(located, match.start() + offset)
            out.append(Candidate(
                path=path, line=line, col=col,
                pattern_id=pattern.id, label=pattern.label, kind=pattern.kind,
                text=match.group(0), line_text=line_text,
            ))
    return out


def _encoded_matches(text: str, path: str, table=None) -> list[Candidate]:
    """Credentials hidden one base64 layer down.  Ported from V3's encoded_scan."""
    out: list[Candidate] = []
    for match in _ENCODED_RE.finditer(text):
        token = match.group(1)
        if len(token) % 4 == 1:
            continue
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if not decoded:
            continue
        inner = _direct_matches(decoded, path, table=table)
        if not inner:
            continue
        start = match.start(1)
        line, col, line_text = _line_col(text, start)
        first = inner[0]
        out.append(Candidate(
            path=path, line=line, col=col,
            pattern_id=f"encoded_{first.pattern_id}",
            label=f"Base64-encoded {first.label}",
            kind=first.kind,
            text=first.text,          # judge the decoded credential, not the wrapper
            line_text=line_text,
        ))
    return out


def _fold_concatenated_literals(text: str, path: str) -> str:
    """Rewrite `"a" + "b"` as `"ab"` before scanning.

    A bypass fixture split a provider key at the twentieth character. Neither
    half matches anything -- the prefix is too short to be a token and the tail
    is just base64 -- so the file scanned clean while holding the whole key.
    Splitting a literal is the cheapest evasion there is and the only one that
    needs no thought.

    Folded onto one line so the reported line number still points somewhere
    real. Python only; other languages keep the plain text path, and that limit
    is in the checker's docstring rather than assumed away.
    """
    if not path.endswith(".py"):
        return text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    def _fold(node):
        """The string this expression evaluates to, or None.

        Written out rather than delegated to `ast.literal_eval`, which folds
        numeric addition and refuses string concatenation -- so the first
        version of this silently folded nothing at all.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = _fold(node.left), _fold(node.right)
            if left is not None and right is not None:
                return left + right
        if isinstance(node, ast.JoinedStr):
            parts = [_fold(v) for v in node.values]
            return "".join(x for x in parts if x is not None) or None
        return None

    extra, seen = [], set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
            continue
        folded = _fold(node)
        if folded and len(folded) > 16 and folded not in seen:
            seen.add(folded)
            extra.append(f"#folded {folded}")
    return text + ("\n" + "\n".join(extra) if extra else "")


def scan_text(text: str, *, path: str, table=None) -> list[Candidate]:
    """Every credential-shaped match in one file's text, deterministically ordered."""
    text = _fold_concatenated_literals(text, path)
    return sorted(_direct_matches(text, path, table=table)
                  + _encoded_matches(text, path, table=table))


def analyse_source(text: str, *, path: str, table=None) -> Report:
    """Split one file's matches into real findings and classified suppressions.

    `table` is the pattern set to judge by, defaulting to the shipped one.
    `extend` used to mutate `PATTERNS` in place, so this answered differently
    depending on whether some earlier caller in the same process had called it
    -- and the two production callers are different processes:
    `checkers/secret_scan.py` unions the adopter's `.v4/secret_patterns.json`
    in, `kernel/engagement.py` does not. So `engagement`'s docstring claim
    ("judged by the same classifier `checkers/secret_scan.py` uses") was false
    for exactly the families an adopter added, and a sentence carrying one
    passed the engagement gate and then made `.v4/ledger_export.jsonl`
    uncommittable -- which is the failure the refusal text warns about.

    `external_write.analyse_source` takes `table=` for the same reason: one
    rule, one input, no hidden state.
    """
    waived = waived_lines(text)
    findings: list[Verdict] = []
    suppressed: list[Verdict] = []
    for candidate in scan_text(text, path=path, table=table):
        if candidate.line in waived:
            judgement = Judgement(False, RULE_PRAGMA,
                                  "an explicit pragma: allow-secret on or above this "
                                  "line waives it")
        else:
            judgement = judge(candidate, table)
        (findings if judgement.is_secret else suppressed).append(Verdict(candidate, judgement))
    return Report(tuple(sorted(findings)), tuple(sorted(suppressed)))
