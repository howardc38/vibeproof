GREEN. From V3 findings #19, #20, #27, #28, #30, #31, #40, #48, #49, #61 --
docstrings and comments across botocore, fsspec, urllib3, pydantic and
opentelemetry. Every one names the slot instead of filling it.
Rules: placeholder-word, template-placeholder.

    :param proxy_url: The proxy url, i.e. https://username:password@proxy.com
    :return: Masked proxy url, i.e. https://***:***@proxy.com
    smb://workgroup;user:password@server:port/share/folder/file.csv
    smb://myuser:mypassword@myserver.com/share/folder/file.csv
    hdfs://username:pwd@node:123/mnt/datasets/test.csv?q=1
    export HTTPS_PROXY='http://username:password@proxy_uri:port'
    proxy_url="socks5h://<username>:<password>@proxy-host"
    # "https://username:password@host.com:80/path?query#fragment"
    http://samuel:pass@example.com:8000/the/path/
