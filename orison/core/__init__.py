"""Core orchestration, with lazy exports to keep storage imports independent."""

__all__ = ["MessageResult", "SessionManager", "SessionRecord"]


def __getattr__(name: str):
    if name in __all__:
        from orison.core import session

        return getattr(session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
