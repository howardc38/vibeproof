"""GREEN -- a write whose read-back is the point of the function.

The shape of a real read-back, with the adopter's domain removed.
``keyring.set_password`` is followed by ``keyring.get_password`` and a
constant-time comparison, and the function raises when they disagree: the write
is confirmed by reading the value back out of the Keychain, which is precisely
what the ``readback`` variant asks for.

It is also green on the ``replay`` side, and for a different reason worth having
under test: storing the same secret twice leaves the same state, so
``set_password`` is not in :data:`~kernel.analysis.external_write.REPLAY_VERBS`
at all.
"""

import sys
from hmac import compare_digest

import keyring

KEYCHAIN_SERVICE = "example-provider-secrets"


class SecureStoreError(RuntimeError):
    pass


def write_provider_secret(account: str, value: str) -> None:
    """Store one provider secret in Keychain without exposing its value."""

    if sys.platform != "darwin":
        raise SecureStoreError("provider secret migration requires macOS Keychain")
    if not value:
        raise ValueError("provider secret value must be non-empty")
    try:
        keyring.set_password(KEYCHAIN_SERVICE, account, value)
        stored_value = keyring.get_password(KEYCHAIN_SERVICE, account)
    except keyring.errors.KeyringError as exc:
        raise SecureStoreError(f"macOS Keychain write failed for account {account}") from exc
    if stored_value is None or not compare_digest(stored_value, value):
        raise SecureStoreError(f"macOS Keychain read-back failed for account {account}")
