"""GREEN -- a real false positive this rule used to produce.

The shape of that false positive, with the adopter's domain removed and the
control flow kept.  ``staged.append(...)`` is an expression statement, so a rule
that reads only the statement's type calls the upload's return value discarded.
It is not: every uploaded object goes into ``staged``, which the caller then
hands to ``_verify_staged_is_fetchable`` and to the publish.

This was the single largest false-positive class on the reference adopter before
:func:`kernel.analysis.external_write._outcome_is_observed` learned about
retaining calls, and it is here so it cannot come back.
"""


class DispatchWorker:
    def __init__(self, blob_adapter) -> None:
        self.blob_adapter = blob_adapter

    def _stage_uploads(self, account_ctx, target, item_rows: list, staged: list) -> None:
        """Upload items to blob storage -- appends to caller-provided ``staged``
        list so partial successes survive mid-loop exceptions and can be cleaned
        by the caller's finally block."""
        for row in item_rows:
            local_path = account_ctx.workspace_root / row.local_rel_path
            staged.append(
                self.blob_adapter.upload_file(local_path, run_id=f"{target.record_id}/{row.asset_id}")
            )
        self._verify_staged_is_fetchable(target, staged)

    def _verify_staged_is_fetchable(self, target, staged: list) -> None:
        if not staged:
            return
        self.blob_adapter.verify_anonymously_readable(staged[0].public_url)
