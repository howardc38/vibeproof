# GREEN. From V3 findings #42, #44, #62, #63 and #64 --
# opentelemetry/semconv/attributes/url_attributes.py and
# streamlit/{connections/sql_connection,runtime/connection_factory}.py. Output
# that has already been through a redactor, and a docstring using xxx as the
# universal "some value". Rules: user-equals-password, placeholder-word.

REDACTED_EXAMPLE = "https://REDACTED:REDACTED@www.example.com/"
DOCSTRING_URL = "xxx+xxx://xxx:xxx@xxx:xxx/xxx"
