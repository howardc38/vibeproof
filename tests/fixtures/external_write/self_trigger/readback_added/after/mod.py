import requests
def notify(msg):
    resp = requests.post(URL, json=msg)
    confirmed = requests.get(f'{URL}/{resp.json()["id"]}')
    return confirmed.json()['status']
