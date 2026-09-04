from core.models import Campaign


KNOWN = {"Campaign": Campaign}


def lookup(name):
    return KNOWN.get(name)
