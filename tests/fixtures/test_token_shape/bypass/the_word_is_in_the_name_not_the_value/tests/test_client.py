FAKE_TEST_TOKEN = "123:SECRET"


def test_x():
    assert Client(bot_token=FAKE_TEST_TOKEN)
