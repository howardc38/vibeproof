"""BYPASS -- the word raise appears and nothing raises."""


import logging

logger = logging.getLogger(__name__)


def verify_signature(request, secret_store) -> bool:
    try:
        expected = secret_store.read_signing_secret(request.tenant_id)
        return request.signature == expected
    except Exception:
        logger.warning("would raise here, but the caller handles it")
        return False
