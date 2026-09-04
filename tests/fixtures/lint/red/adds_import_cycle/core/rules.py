from core.models import Campaign


def validate(obj):
    return isinstance(obj, Campaign)
