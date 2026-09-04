import requests


def go(url):
    return requests.post(url, json={})
