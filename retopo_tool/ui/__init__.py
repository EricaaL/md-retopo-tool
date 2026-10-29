"""User interface. session.py is plain Python; window.py needs Maya's Qt (open it with show())."""


def show():
    from .window import show as _show
    return _show()
