"""RED -- readback.  Assigning the response is not the same as reading it.

``resp`` is never loaded again, so the status code, the body and any error the
server reported are all discarded -- the assignment only silences the linter
that complained about the bare call.  A 200 with ``{"ok": false}`` in the body
leaves this function believing the notification went out.
"""


class Webhook:
    def __init__(self, client, url: str) -> None:
        self.client = client
        self.url = url

    def notify(self, event: dict) -> None:
        resp = self.client.post(self.url, json=event, timeout=10)
        self.record_attempt(event["id"])

    def record_attempt(self, event_id: str) -> None:
        self.attempts = getattr(self, "attempts", []) + [event_id]
