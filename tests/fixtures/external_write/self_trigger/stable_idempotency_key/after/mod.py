import hashlib, json
def charge(order):
    key = hashlib.sha256(json.dumps(order, sort_keys=True).encode()).hexdigest()
    resp = http.post(URL, json={'idempotencyKey': key, 'o': order})
    return resp.json()['id']
