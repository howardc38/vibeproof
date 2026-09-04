def notify(name):
    if len(name) > 8:
        name = name[:8]
    return f'[{name}] hi'
