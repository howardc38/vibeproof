import requests
def notify(msg):
    try:
        requests.post(URL, json=msg)
    except Exception as e:
        log.warning('failed: %s', e)
    return True
