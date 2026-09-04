"""RED -- readback.  The publish happens and its answer is thrown away.

``publish`` returns the created media's id.  Dropping it leaves the row with no
marker, so nothing downstream can tell this post from one that was never
published, and the next attempt publishes again.  The scope calls nothing from
the outbound-read table either, so there is no second source of the answer.
"""


class PostingWorker:
    def __init__(self, meta_adapter, post_repo) -> None:
        self.meta_adapter = meta_adapter
        self.post_repo = post_repo

    def run(self, target, staged, media_types) -> None:
        self.meta_adapter.publish(
            post_id=target.post_id,
            media_urls=[item.public_url for item in staged],
            media_types=media_types,
            caption=target.caption_final,
            post_type=target.post_type,
        )
        self.post_repo.mark_done(target.post_id)
