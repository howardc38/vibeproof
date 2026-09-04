"""RED, and the most important fixture in this directory.

The path says ``test_``. The value is still a live-shaped GitHub PAT. Any
scanner that exempts test paths passes this file, and adopter_a has already
had exactly this leak -- ``runtime/gitleaks-review-redacted.json`` records
"replace real Shopify token with fake in test fixture --
tests/integrations/mcp/test_bridge.py:30 contained the user-provided real
token". Location is never evidence; only the value is.
"""

GITHUB_TOKEN_FOR_LIVE_RUN = "ghp_cpE1ckI4yZ9gQCuVrxjXZRF3Kk56s0jZ3tSj"
