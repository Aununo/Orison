"""Core orchestration package.

Core owns Orison sessions and context assembly. It talks to providers through
the provider registry rather than calling any provider-specific CLI directly.
"""
"""Core orchestration for Orison."""

from orison.core.session import MessageResult, SessionManager, SessionRecord

__all__ = ["MessageResult", "SessionManager", "SessionRecord"]
