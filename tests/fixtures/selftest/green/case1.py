def send_1():
    try:
        requests.post("https://api.example.com/x")
    except Exception:
        log.exception("failed")
        raise
