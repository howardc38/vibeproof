"""RED -- replay.  The file re-executes a create, with nothing to dedupe on.

The shape of a media-analysis call in the reference adopter and its two
siblings: a bounded retry loop around a billable, non-idempotent provider
call.  A transport fault after the provider accepted the request is
indistinguishable from one before, so the second attempt is a second charge --
``docs/FACTS.md`` files LLM inference as a write for exactly this reason, and
``openai_image.py`` sets ``max_retries=0`` on the same grounds.

Nothing in this scope carries a client-supplied identity the server could dedupe
on, so the retry cannot be made safe from here.
"""

import time

import requests


class InferenceProvider:
    MAX_RETRIES = 3

    def __init__(self, endpoint: str, model: str) -> None:
        self.endpoint = endpoint
        self.model = model

    def generate(self, prompt: str) -> dict:
        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = requests.post(
                    self.endpoint,
                    json={"model": self.model, "prompt": prompt},
                    timeout=120,
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"inference failed after {self.MAX_RETRIES} attempts") from last_error
