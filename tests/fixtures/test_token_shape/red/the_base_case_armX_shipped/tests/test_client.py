from app.client import Client


def test_send():
    c = Client(bot_token="123:SECRET")
    assert c
