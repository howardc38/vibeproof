"""GREEN -- the answer a ``readback`` claim is closed with.

This is the file ``red/publish_result_dropped.py`` becomes once the claim it
raises is answered: the write is followed by a read of the outside world that
establishes the effect landed, and the function refuses rather than continuing
when it did not.

It doubles as the convergence case.  Answering the claim added an outbound
*read* and a bookkeeping write (``mark_published``), and neither raises a new
claim of this kind: ``kernel.facts`` will not let a read pattern be a write
pattern, and the analysis suppresses every unobserved write in a scope that
gained a read -- including the one the fix itself introduced.
``tests/test_external_write.py::SelfTrigger`` re-derives over this file to keep
that executable.
"""

import requests


class Publisher:
    def __init__(self, endpoint: str, post_repo) -> None:
        self.endpoint = endpoint
        self.post_repo = post_repo

    def publish_and_confirm(self, post_id: str, payload: dict) -> str:
        created = requests.post(
            f"{self.endpoint}/media_publish", json=payload, timeout=30,
        ).json()
        media_id = created.get("id")
        if not isinstance(media_id, str) or not media_id:
            raise RuntimeError("publish returned 2xx without a media id")
        confirmed = requests.get(
            f"{self.endpoint}/{media_id}", params={"fields": "permalink"}, timeout=30,
        ).json()
        if not confirmed.get("permalink"):
            raise RuntimeError(f"published media {media_id} is not readable back")
        self.post_repo.mark_published(post_id, media_id)
        return media_id
