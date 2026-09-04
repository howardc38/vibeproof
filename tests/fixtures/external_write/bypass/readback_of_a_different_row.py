"""BYPASS -- the read-back reads something else.

A call from the outbound-read table is present, so a rule that only asks
"was anything read back" is satisfied. It reads a different post.
"""


class PostingWorker:
    def __init__(self, meta_adapter, post_repo) -> None:
        self.meta_adapter = meta_adapter
        self.post_repo = post_repo

    def run(self, target, staged, media_types) -> None:
        self.meta_adapter.publish(post_id=target.post_id, caption=target.caption_final)
        self.meta_adapter.get_media(post_id='some_other_post')
        self.post_repo.mark_done(target.post_id)
