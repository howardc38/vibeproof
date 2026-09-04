"""RED: a secret store that eats the exception.

The shape ``core/config/secure_store.py`` and ``core/tenants/secret_store.py`` are
called out for in DESIGN.md §10.2: the delete is the security-relevant act, and
``pass`` is the whole handler.  ``rotate`` then writes the new value on top of a
key it never proved it removed.
"""

import keyring

SERVICE = "example-app"


def delete_credential(account: str) -> None:
    try:
        keyring.delete_password(SERVICE, account)
    except Exception:
        pass


def rotate(account: str, new_value: str) -> None:
    delete_credential(account)
    keyring.set_password(SERVICE, account, new_value)
