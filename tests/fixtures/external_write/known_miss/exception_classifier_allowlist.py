"""KNOWN MISS -- the defect chain the reference adopter's fix commit closed, minus the link
this rule can see.

``tests/test_external_write.py::KnownMiss`` asserts the checker exits **0** on
this file.  That assertion is the point: an executable blind spot beats a
paragraph promising one, and it fails the day the rule grows enough to see this.

The chain, at ``f0060ebb^``:

1. ``_media_publish`` gets a 2xx whose body will not parse and raises
   ``ValueError`` (``json.JSONDecodeError`` is a subclass).
2. ``submission_outcome_is_unknown`` answers ``False`` -- its allowlist holds
   only ``Timeout`` and ``ConnectionError``.
3. ``_publish_to_meta`` reads that as "Meta answered, so the media was not
   created" and clears the reconciliation baseline.
4. The retry has no baseline to reconcile against, so it publishes the post a
   second time.

**Why no AST rule reaches it.**  Link 2 is a closed ``isinstance`` allowlist over
exception classes, and so is the fixed version -- ``f0060ebb`` adds
``ChunkedEncodingError``, ``ContentDecodingError`` and
``MetaPublishOutcomeUnknown`` to the same tuple in the same function.  The two
files are the same construct with different tuple members; nothing structural
separates the safe answer from the unsafe one.  Link 3 is identical before and
after the fix, so a rule that fired on "the handler clears a dedupe guard" would
fire on the corrected code too -- an unanswerable claim, which is worse than a
miss.

What this checker *does* catch from the same commit is link 1's sibling: the
``return set()`` in ``_recent_media_ids``, shipped as
``red/readback_returns_empty_when_unreadable.py`` and its fixed twin.  One of
three causes, named, not all three.

Closing this properly needs a rule about *which answer a classifier defaults
to*, which is a different claim kind, not a wider version of this one.
``kernel/analysis/fail_closed.py`` reaches the same conclusion about the same
function from the other side.
"""

import requests


class MetaPublishError(RuntimeError):
    pass


class MetaGraphAdapter:
    def submission_outcome_is_unknown(self, exc: BaseException) -> bool:
        """True when ``exc`` leaves it genuinely unknown whether the publish landed.

        Only a transport-level interruption is unknown: either Meta never received
        the request, or it answered and the answer never arrived.  Anything Meta
        actually answered is a known answer, and the media was not created.
        """
        return isinstance(
            exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
        )


class PostingWorker:
    def __init__(self, meta_adapter, post_repo) -> None:
        self.meta_adapter = meta_adapter
        self.post_repo = post_repo

    def _publish_to_meta(self, target, staged: list, media_types):
        self._open_submission_baseline(target)
        try:
            result = self.meta_adapter.publish(
                post_id=target.post_id,
                media_urls=[item.public_url for item in staged],
                media_types=media_types,
                caption=target.caption_final or target.caption_draft,
                post_type=target.post_type,
            )
        except Exception as exc:
            # The baseline only earns its keep while the outcome is unknown. Meta
            # having answered -- even with an error -- is an answer, and holding a
            # stale baseline past it would let the *next* post's media reconcile
            # onto this one.
            if not self.meta_adapter.submission_outcome_is_unknown(exc):
                self._close_submission_baseline(target)
            raise
        self._close_submission_baseline(target)
        return result

    def _open_submission_baseline(self, target) -> None:
        self.post_repo.open_meta_submission(target.post_id, brand_id=target.brand_id)

    def _close_submission_baseline(self, target) -> None:
        self.post_repo.close_meta_submission(target.post_id, brand_id=target.brand_id)
