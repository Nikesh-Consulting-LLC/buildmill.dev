"""ACP — the Agent Client Protocol side of the runner (US-78.2).

ACP is Zed's [Agent Client Protocol](https://agentclientprotocol.com), not an
xAI protocol: JSON-RPC 2.0 over a child's stdin/stdout, `protocolVersion` 1, one
process multiplexing many sessions. The Buildmill Interactive Agent is driven
through it; every other module in this runner is a one-shot command line.

`client` is the transport and the agent-facing calls; `events` turns the
`session/update` notifications into the progress lines the rest of the runner
already speaks.
"""

from .client import (
    PROTOCOL_VERSION,
    AcpClient,
    AcpError,
    client_capabilities,
)
from .events import describe_update

__all__ = [
    "PROTOCOL_VERSION",
    "AcpClient",
    "AcpError",
    "client_capabilities",
    "describe_update",
]
