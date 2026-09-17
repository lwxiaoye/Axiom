"""Run backend tests without connecting to deployed databases or external services.

Usage: agent-api/.venv/Scripts/python.exe scripts/audit_backend_offline.py [pytest args]
Only loopback sockets bound to ephemeral ports by this process may be contacted.
This guards Python socket traffic; it is not an OS sandbox for subprocesses.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT / "agent-api")
sys.path.insert(0, str(ROOT / "agent-api"))
os.environ.update({
    "DATABASE_URL": "mysql+aiomysql://test:test@127.0.0.1:1/test",
    "CHECKPOINT_DATABASE_URL": "",
    "RUNTIME_DATABASE_URL": "",
    "RUNTIME_REQUIRED": "false",
    "MIGRATE_ON_STARTUP": "false",
})

allowed_ports: set[int] = set()
original_bind = socket.socket.bind
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_getaddrinfo = socket.getaddrinfo


def guarded_bind(sock, address):
    result = original_bind(sock, address)
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1") and address[1] == 0:
        allowed_ports.add(sock.getsockname()[1])
    return result


def check_destination(address):
    if not (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")
            and address[1] in allowed_ports):
        raise OSError("Offline audit blocked a connection outside an in-process test server")


def guarded_connect(sock, address):
    check_destination(address)
    return original_connect(sock, address)


def guarded_connect_ex(sock, address):
    check_destination(address)
    return original_connect_ex(sock, address)


def guarded_getaddrinfo(host, *args, **kwargs):
    if host not in (None, "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"):
        raise OSError("Offline audit blocked external DNS resolution")
    return original_getaddrinfo(host, *args, **kwargs)


socket.socket.bind = guarded_bind
socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
socket.getaddrinfo = guarded_getaddrinfo

import pytest  # noqa: E402

if __name__ == "__main__":
    # Windows multiprocessing imports this module as __mp_main__ in child
    # processes; it must not recursively launch another full pytest run.
    raise SystemExit(pytest.main(sys.argv[1:] or ["tests", "-q"]))
