def send_2():
    try:
        requests.post("https://api.example.com/x")
    except Exception:
        log.exception("failed")
        raise
