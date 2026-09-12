from . import mail

def go():
    return mail.send('fixture@example.invalid')
