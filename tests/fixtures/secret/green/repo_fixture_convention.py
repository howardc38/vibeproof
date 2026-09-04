# GREEN. The fixture vocabulary a real adopter had already settled on across
# four of its own test files: values whose whole job is to mean "a token goes
# here" without being one. Kept because a scanner that fires on these fires on
# every careful test suite, and the four filenames are gone because naming them
# maps somebody's tree.
# Rules: declared-fake-marker, placeholder-word.

SHOPIFY = "shpat_test_redact_fixture_abcdef1234567890"
SHOPIFY_LONG = "shpat_test_shopify_redact_fixture_abcdef123456"
SHOPIFY_ROUTE = "shpat_test_supersecret_value_xyz123fake"
GOOGLE = "AIza_test_redact_fixture_FakeGoogleApiKey0123456789"
STRIPEISH = "sk-live-not-a-real-secret-value-for-tests-only"

# V3 kept a separate `fake_prefix` allowlist for these. declared-fake-marker
# subsumes it: every prefix in that table contains the word "test".
V3_FAKE_OPENAI = "sk-test-0123456789abcdef0123456789abcdef"
V3_FAKE_ANTHROPIC = "sk-ant-test-0123456789abcdef0123456789abcdef"
V3_FAKE_AWS = "AKIATESTABCDEFGHIJKL"
V3_FAKE_GITHUB = "github_pat_test_0123456789abcdef0123456789"
V3_FAKE_SLACK = "xoxb-test-0123456789abcdef"
V3_FAKE_GOOGLE = "AIzaTest0123456789abcdef0123456789ABC"
