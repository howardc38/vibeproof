"""KNOWN MISS: the defect at ``adopter_a`` ``f0060ebb^``, distilled to nothing else.

``tests/test_fail_closed.py::test_known_miss_*`` asserts the checker returns **0**
on this file.  That assertion is the point: the miss is an executable fact, not a
paragraph.  If the rule ever grows to catch this construct, that test fails and
somebody has to move this file into ``red/``.

Why it is not in ``red/``: the mechanical acceptance runs "every file under red/
exits 1".  A file that is known to score 0 cannot live there without breaking the
invariant that makes the rest of the suite meaningful.

--------------------------------------------------------------------------------
The construct
--------------------------------------------------------------------------------

**A closed-world ``isinstance`` allowlist used as a safety predicate, whose
default answer is the unsafe one, consulted by a handler that does re-raise.**

Trace it in the code below:

1. ``_request`` gets a 2xx whose body will not parse and raises ``ValueError``.
   That is correct, fail-closed behaviour -- it refuses to invent a result.
2. ``submission_outcome_is_unknown`` is an ``isinstance`` tuple of two transport
   exceptions.  ``ValueError`` is not in it, so the answer is ``False``:
   *"the outcome is known"*.  Nothing in the program said that; the tuple did,
   by omission.
3. ``publish_post``'s handler re-raises -- it fails closed by every structural
   test this rule applies -- but before it does, it drops the reconciliation
   baseline, because step 2 told it the outcome was known.
4. The retry republishes. The post goes out twice.

--------------------------------------------------------------------------------
Why this rule cannot see it
--------------------------------------------------------------------------------

Every ingredient is individually fail-closed.  There is no swallowing handler, no
falsey return on an error path, no suppressed exception.  ``exit_kind`` on the
handler is ``EXIT_RAISE``.  The defect is not in *whether* the exception
propagates -- it does -- but in a **destructive side effect performed before
propagating, gated on an enumeration that is wrong by omission**.

Deciding that ``False`` is the unsafe default requires knowing which answer is
safe, and no AST carries that.  The honest fix is a different claim kind (an
``exception-classification`` detector that raises a claim for every
exception-typed predicate so a reviewer answers "is the default the safe one?"),
not a wider fail-closed rule -- widening this one to "any isinstance tuple over
exceptions" would fire on every correct retry classifier in the repo.
"""

import json

import requests


class MetaPublishOutcomeUnknown(RuntimeError):
    pass


def _request(method: str, url: str, params: dict) -> dict:
    response = requests.request(method, url, params=params, timeout=120)
    try:
        return response.json()
    except ValueError:
        if response.status_code >= 400:
            raise RuntimeError(f"meta error status={response.status_code}") from None
        # A 2xx whose body will not parse. Refusing to invent a result is right;
        # what follows is what turns it into a duplicate post.
        raise


def submission_outcome_is_unknown(exc: BaseException) -> bool:
    """The whole defect. Wrong by omission, and nothing here looks wrong."""
    return isinstance(
        exc,
        (requests.exceptions.Timeout, requests.exceptions.ConnectionError),
    )


class Publisher:
    def __init__(self, repo) -> None:
        self.repo = repo

    def publish_post(self, post) -> dict:
        self.repo.open_submission_baseline(post)
        try:
            return _request("POST", "https://graph.example/media_publish", {"id": post.id})
        except Exception as exc:
            # Structurally impeccable: it re-raises. The damage is on the line above.
            if not submission_outcome_is_unknown(exc):
                self.repo.close_submission_baseline(post)
            raise

    def retry(self, post) -> dict:
        if self.repo.baseline_for(post) is None:
            # No baseline left to reconcile against, so this republishes.
            return self.publish_post(post)
        return json.loads("{}")
