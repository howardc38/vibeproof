"""RED (tricky): ``contextlib.suppress`` -- a swallow with no ``except`` at all.

A rule that only looks at ``ast.Try`` scores this file clean, which is why the
analysis walks ``with`` too.  There is nothing here for a reviewer to notice:
the failure has no handler, no log line, and no trace.
"""

import contextlib

import httpx


def deactivate(client: httpx.Client, account_id: str) -> None:
    with contextlib.suppress(Exception):
        client.post(f"/accounts/{account_id}/deactivate")


def revoke(client: httpx.Client, token: str) -> None:
    with contextlib.suppress(httpx.HTTPError):
        client.delete(f"/tokens/{token}")
