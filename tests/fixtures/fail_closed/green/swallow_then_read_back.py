"""Taken from adopter_a `core/config/secure_store.py::delete_provider_secret`.

The handler swallows and the two lines after it are what decide. This was
reported as a fail-open handler with the message "execution continues as if the
operation had succeeded", which the read-back makes false.

Green because the caller does get an exception when the delete did not happen --
which is the whole question the kind asks.
"""

import keyring

SERVICE = "example"


class StoreError(RuntimeError):
    pass


def read_secret(account):
    return keyring.get_password(SERVICE, account)


def delete_secret(account: str) -> None:
    try:
        keyring.delete_password(SERVICE, account)
    except keyring.errors.PasswordDeleteError:
        # The macOS backend raises this for every non-zero status: a locked
        # keychain and a denied access prompt look exactly like "already
        # absent". The read-back below is what decides.
        pass
    if read_secret(account) is not None:
        raise StoreError(f"delete read-back failed for {account}")
