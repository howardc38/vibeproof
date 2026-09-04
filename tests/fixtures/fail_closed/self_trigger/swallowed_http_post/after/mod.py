import requests
def notify(msg):
    try:
        requests.post(URL, json=msg)
    except Exception:
        log.exception('failed')
        raise
    return True
