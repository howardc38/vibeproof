from core.rules import validate


class Campaign:
    def check(self):
        return validate(self)
