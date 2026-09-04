def send_3():
    try:
        requests.post("https://api.example.com/x")
    except Exception:
        log.exception("failed")
        raise
