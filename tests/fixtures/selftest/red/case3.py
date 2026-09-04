def send_3():
    try:
        requests.post("https://api.example.com/x")
    except Exception as e:
        log.warning("failed: %s", e)
    return True
