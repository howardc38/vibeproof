# GREEN. From V3 findings #53-#59, pyarrow/tests/{parquet/conftest,test_dataset,
# test_fs}.py. The credentials are format slots filled at runtime from a fixture.
# Rule: template-placeholder.

def s3_uri(access_key, secret_key, bucket, path):
    return f"s3://{access_key}:{secret_key}@{bucket}/{path}?scheme=http"


TEMPLATE = "s3://{}:{}@{{}}?scheme=http&endpoint_override={}:{}"
