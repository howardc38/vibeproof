"""GREEN -- a fresh value next to an outbound write, and freshness is the point.

The shape of a real false positive, reported from an adopter with the domain
removed.  ``nonce`` was in ``IDEMPOTENCY_WORDS``, so this module's OAuth state
nonce -- generated per call, as a CSRF token must be -- was reported as a
"new-per-call idempotency key" for the token exchange two functions below it.

The two requirements are opposite and one word carried both.  An idempotency
key must be *the same* on a retry, which is why ``red/per_call_idempotency_key``
is a defect.  A CSRF nonce must be *different* every time, and one that repeated
would be the defect.  Nothing here is retried either: the exchange is single-use
by the provider's contract, so a key would have nothing to dedupe.

The write reads its result back, so the readback half has nothing to say about
this file and the only thing under test is the replay half.
"""

import secrets

import httpx

_TOKEN_ENDPOINT = "https://graph.example.com/v21.0/oauth/access_token"


def begin_authorisation(redirect_uri: str) -> tuple[str, str]:
    """The URL to send a person to, and the nonce to check when they come back."""
    state_nonce = secrets.token_urlsafe(32)
    query = httpx.QueryParams(
        {
            "client_id": "app-id",
            "redirect_uri": redirect_uri,
            "state": state_nonce,
            "scope": "pages_manage_posts",
        }
    )
    return f"https://www.example.com/v21.0/dialog/oauth?{query}", state_nonce


def exchange_code_for_token(*, code: str, redirect_uri: str,
                            app_secret: str) -> dict:
    """One-shot: the provider invalidates the code on first use, retry or not."""
    response = httpx.post(
        _TOKEN_ENDPOINT,
        data={
            "client_id": "app-id",
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    if "access_token" not in body:
        raise RuntimeError(f"token exchange returned no token: {sorted(body)}")
    return body
