"""Supervisor runner (US-10.1+): the server-controlled agent runner.

The polling `runner.py` is retired in US-10.10; this package is its
replacement — a persistent control socket (US-10.1), server-pushed config
(US-10.2), a server LLM brain (US-10.3/10.6), pluggable agent modules
(US-10.4/10.5), an audited shell (US-10.7), and self-repair (US-10.8).
"""

from .connection import RunnerConnection, build_hello, heartbeat_frame

__all__ = ["RunnerConnection", "build_hello", "heartbeat_frame"]
