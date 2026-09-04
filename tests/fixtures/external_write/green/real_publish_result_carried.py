"""GREEN -- the publish's answer is what the function hands back.

The shape of a real false positive this rule used to produce, with the adopter's
domain removed and the control flow kept.  ``result`` carries the item id and
the permalink out to the caller, which writes the idempotency marker with it --
so the write's outcome is observed, by value, in this scope.

Kept as a green fixture on purpose even though the *known miss* lives one
function away: what this rule can see about this code really is clean, and
saying so is what makes ``known_miss/exception_classifier_allowlist.py`` a
statement about the rule rather than about the code.
"""


class DispatchWorker:
    def __init__(self, feed_adapter, record_repo) -> None:
        self.feed_adapter = feed_adapter
        self.record_repo = record_repo

    def _publish_to_feed(self, target, staged: list, item_types):
        def _on_item_published(item_id: str) -> None:
            self.record_repo.set_published_item_id_and_close_submission(
                target.record_id, item_id, account_id=target.account_id,
            )

        result = self.feed_adapter.publish(
            record_id=target.record_id,
            item_urls=[item.public_url for item in staged],
            item_types=item_types,
            caption=target.caption_final or target.caption_draft,
            record_type=target.record_type,
            on_item_published=_on_item_published,
        )
        return result
