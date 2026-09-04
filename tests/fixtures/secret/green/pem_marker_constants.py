# GREEN. From V3 findings #21, #32 and #33 --
# cryptography/hazmat/primitives/serialization/ssh.py and
# google/auth/crypt/_python_rsa.py. The BEGIN and END markers are string
# constants used to *recognise* a key; there is no key between them.
# Rule: pem-without-key-material.

_PKCS1_MARKER = ("-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----")
_PKCS8_MARKER = ("-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----")
_SK_START = b"-----BEGIN OPENSSH PRIVATE KEY-----"
_SK_END = b"-----END OPENSSH PRIVATE KEY-----"
