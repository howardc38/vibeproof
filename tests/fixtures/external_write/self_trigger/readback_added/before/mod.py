import requests
def notify(msg):
    requests.post(URL, json=msg)
