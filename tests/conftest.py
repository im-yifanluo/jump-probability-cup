"""Suite-wide guard: no test may open a network connection.

Everything that talks to the outside world is exercised through the golden
harness, which runs in its own process against a fake network."""
import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", refuse)
