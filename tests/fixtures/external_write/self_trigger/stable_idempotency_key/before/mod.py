import uuid
def charge(order):
    return http.post(URL, json={'idempotencyKey': uuid.uuid4().hex, 'o': order})
