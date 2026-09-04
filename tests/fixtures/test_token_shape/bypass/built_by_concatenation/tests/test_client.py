def test_x():
    tok = "123:" + "SECRET" + "VALUE9"
    assert Client(bot_token=tok)
