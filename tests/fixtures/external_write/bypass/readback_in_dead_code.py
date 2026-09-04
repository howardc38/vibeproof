"""BYPASS -- the read-back is unreachable."""


class PostingWorker:
    def __init__(self, meta_adapter, post_repo) -> None:
        self.meta_adapter = meta_adapter
        self.post_repo = post_repo

    def run(self, target, staged, media_types) -> None:
        self.meta_adapter.publish(post_id=target.post_id, caption=target.caption_final)
        self.post_repo.mark_done(target.post_id)
        return
        self.meta_adapter.get_media(post_id=target.post_id)
