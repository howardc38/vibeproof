from mod import f


def outer():
    def inner():
        return f(1)
    return inner
