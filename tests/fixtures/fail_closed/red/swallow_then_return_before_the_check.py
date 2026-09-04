"""The exemption next door, with the one thing that makes it not apply.

`swallow_then_read_back.py` is green because control reaches a check that
raises. Here the handler returns first, so the check below it never runs on the
swallowing path -- and the caller is told the delete succeeded when it did not.

This exists so the exemption cannot quietly become "any function with a raise
somewhere below the try".
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
        return
    if read_secret(account) is not None:
        raise StoreError(f"delete read-back failed for {account}")
