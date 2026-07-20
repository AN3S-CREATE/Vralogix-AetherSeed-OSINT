"""Audit-log hash-chain tests."""

from __future__ import annotations

from aetherseed.core.storage.audit import AuditLog


def test_chain_verifies(env) -> None:
    log = AuditLog("run_test", env)
    log.emit("a.event", x=1)
    log.emit("b.event", y=2)
    log.emit("c.event", z=3)
    assert log.verify_chain() is True
    assert len(log.read_all()) == 3


def test_tampering_is_detected(env) -> None:
    log = AuditLog("run_tamper", env)
    log.emit("a.event", x=1)
    log.emit("b.event", y=2)
    # Corrupt the first line.
    lines = log.path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"x": 1', '"x": 999')
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_chain() is False
