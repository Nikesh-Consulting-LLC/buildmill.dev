"""Server-side SSH/SFTP bridge (US-1.28–1.30, US-1.46).

Everything that touches a registered server goes through here: `api` reads
the stored credentials from the private `data` bucket, opens the SSH
connection with paramiko, and reuses that one transport for an interactive
PTY (terminal), SFTP (file manager / editor), and exec channels (unzip).
Credentials never leave this process; the browser only ever sees I/O.

Host keys are trust-on-first-use: the first successful connect records the
remote host key fingerprint on the server row; any later connect that sees
a different fingerprint raises HostKeyChanged and refuses to proceed until
an operator explicitly re-trusts it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import socket
from dataclasses import dataclass

import paramiko

CONNECT_TIMEOUT = 12


class SSHError(Exception):
    """A connection/auth failure with a human-readable, safe message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class HostKeyChanged(SSHError):
    """The remote host key no longer matches the trusted fingerprint."""

    def __init__(self, presented_fingerprint: str):
        super().__init__(
            "The server's host key has changed since it was first trusted. "
            "This can mean the server was rebuilt — or that the connection is "
            "being intercepted. The session was refused."
        )
        self.presented_fingerprint = presented_fingerprint


@dataclass
class Credentials:
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


def _fingerprint(blob: bytes) -> str:
    """OpenSSH-style SHA256 fingerprint of a key's wire bytes."""
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def public_key_fingerprint(private_key_pem: str, passphrase: str | None) -> str:
    """Fingerprint of the public half of a pasted private key.

    This is the only readable trace we keep of a stored SSH key.
    """
    key = load_private_key(private_key_pem, passphrase)
    return _fingerprint(key.asbytes())


def load_private_key(pem: str, passphrase: str | None) -> paramiko.PKey:
    """Parse a pasted private key, trying each supported algorithm."""
    password = passphrase or None
    errors: list[str] = []
    for key_cls in (
        paramiko.Ed25519Key,
        paramiko.ECDSAKey,
        paramiko.RSAKey,
    ):
        try:
            return key_cls.from_private_key(io.StringIO(pem), password=password)
        except paramiko.PasswordRequiredException:
            raise SSHError("This private key is encrypted — a passphrase is required.")
        except paramiko.SSHException as e:
            errors.append(str(e))
            continue
    raise SSHError("Could not read that private key (unsupported format or wrong passphrase).")


def _connect_transport(host: str, port: int) -> paramiko.Transport:
    try:
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        # Nagle's algorithm batches small writes waiting up to ~40ms (or a
        # full RTT with delayed ACKs) for more data before sending — exactly
        # the wrong tradeoff for an interactive terminal, where every
        # keystroke is its own tiny packet that wants to leave immediately.
        # This is the single most common cause of "typing feels laggy" over
        # a raw socket.create_connection() (Python enables Nagle by default;
        # nothing else in this path disables it downstream).
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except socket.gaierror:
        raise SSHError(f"Could not resolve host '{host}'.")
    except ConnectionRefusedError:
        raise SSHError(f"Connection refused by {host}:{port}.")
    except (socket.timeout, TimeoutError):
        raise SSHError(f"Connection to {host}:{port} timed out.")
    except OSError as e:
        raise SSHError(f"Could not reach {host}:{port}: {e.strerror or e}.")

    transport = paramiko.Transport(sock)
    transport.set_keepalive(30)
    try:
        transport.start_client(timeout=CONNECT_TIMEOUT)
    except paramiko.SSHException as e:
        transport.close()
        raise SSHError(f"SSH handshake failed: {e}")
    return transport


@dataclass
class Connection:
    transport: paramiko.Transport
    host_key_fingerprint: str

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass


def open_connection(
    *,
    host: str,
    port: int,
    username: str,
    auth_method: str,
    creds: Credentials,
    expected_host_fingerprint: str | None,
) -> Connection:
    """Open + authenticate a transport, enforcing host-key trust.

    Blocking; call from a worker thread. On success the caller should
    persist ``host_key_fingerprint`` if it wasn't already stored.
    """
    transport = _connect_transport(host, port)
    host_key = transport.get_remote_server_key()
    presented = _fingerprint(host_key.asbytes())

    if expected_host_fingerprint and expected_host_fingerprint != presented:
        transport.close()
        raise HostKeyChanged(presented)

    try:
        if auth_method == "password":
            if not creds.password:
                raise SSHError("No stored password for this server.")
            transport.auth_password(username, creds.password)
        else:
            if not creds.private_key:
                raise SSHError("No stored SSH key for this server.")
            pkey = load_private_key(creds.private_key, creds.passphrase)
            transport.auth_publickey(username, pkey)
    except paramiko.AuthenticationException:
        transport.close()
        raise SSHError("Authentication rejected by the server.")
    except paramiko.SSHException as e:
        transport.close()
        raise SSHError(f"Authentication error: {e}")

    if not transport.is_authenticated():
        transport.close()
        raise SSHError("Authentication rejected by the server.")

    return Connection(transport=transport, host_key_fingerprint=presented)
