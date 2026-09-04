"""RED (falsey): a signature check that answers False when it could not check.

"The signature is invalid" and "I could not verify the signature" are different
answers, and returning False for both is how an outage becomes an authorization
decision.  This is the ``falsey`` variant.
"""

import logging

logger = logging.getLogger(__name__)


def verify_signature(request, secret_store) -> bool:
    try:
        expected = secret_store.read_signing_secret(request.tenant_id)
        return request.signature == expected
    except Exception:
        logger.warning("signature verification unavailable")
        return False


def has_permission(user, action) -> bool:
    try:
        return user.permissions.can(action)
    except Exception:
        return None
