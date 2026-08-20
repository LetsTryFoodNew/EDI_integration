"""
Guard against RQ queue-name drift between the code and docker-compose.yml.

Why this exists: on 2026-08-20 the outbound queue was found holding 10,778 unread
jobs. The code enqueued to `outbound` and `sap_push`; the compose file ran workers
on `parse` and `sap`. No 855 ACK or 856 ASN had ever been sent, and nothing
surfaced it — a queued-but-unconsumed message looks exactly like a message that
is about to be sent, and the SLA monitor only flags sends that were *attempted*.

A typo in either place is silent in production and loud here.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "app"
_COMPOSE = _ROOT / "docker-compose.yml"

# Workers deliberately listening on a queue nothing currently produces to. Adding
# a name here is a decision to keep an idle container running — say why.
_KNOWN_IDLE: dict[str, str] = {
    # Every parse job is enqueued to `ingest` (app/workflows/parse_and_persist.py).
    # Repointing this worker at `ingest` would double concurrency against
    # rate-limited partner APIs, so it is a separate call to make.
    "parse": "parse jobs are enqueued to `ingest`; see CHANGELOG 2026-08-20",
}


def _queue_name_constants() -> dict[str, str]:
    """Resolve `FOO_QUEUE_NAME = "foo"` module constants so Queue(FOO_QUEUE_NAME) works."""
    consts: dict[str, str] = {}
    for py in _APP.rglob("*.py"):
        for name, value in re.findall(
            r'^([A-Z_]*QUEUE_NAME)\s*=\s*["\']([a-z_]+)["\']', py.read_text(), re.MULTILINE
        ):
            consts[name] = value
    return consts


def _produced_queues() -> dict[str, set[str]]:
    """Queue names the application enqueues to → the files that do it."""
    consts = _queue_name_constants()
    produced: dict[str, set[str]] = {}

    for py in _APP.rglob("*.py"):
        text = py.read_text()
        rel = str(py.relative_to(_ROOT))
        for arg in re.findall(r"\bQueue\(\s*([A-Za-z_\"'][\w\"']*)", text):
            name = consts.get(arg) if arg.isidentifier() else arg.strip("\"'")
            if name:
                produced.setdefault(name, set()).add(rel)
    return produced


def _consumed_queues() -> dict[str, set[str]]:
    """Queue names an `rq worker` command in docker-compose.yml listens on → its command."""
    consumed: dict[str, set[str]] = {}
    for cmd in re.findall(r"^\s*command:\s*(\[.*\])\s*$", _COMPOSE.read_text(), re.MULTILINE):
        tokens = re.findall(r'"([^"]+)"', cmd)
        if tokens[:2] != ["rq", "worker"]:
            continue
        for tok in tokens[2:]:
            if tok.startswith("-"):
                break
            consumed.setdefault(tok, set()).add(cmd)
    return consumed


def test_every_produced_queue_has_a_worker() -> None:
    """A queue the code enqueues to with no worker silently swallows work."""
    produced = _produced_queues()
    consumed = _consumed_queues()

    assert produced, "found no Queue(...) call sites — the scanner is broken, not the wiring"

    orphaned = {q: sorted(files) for q, files in produced.items() if q not in consumed}
    assert not orphaned, (
        "These queues are enqueued to but no docker-compose worker consumes them, "
        f"so their jobs pile up unread: {orphaned}. "
        f"Workers currently listen on: {sorted(consumed)}."
    )


def test_no_unexpected_idle_workers() -> None:
    """A worker on a queue nothing produces to burns a container for nothing."""
    produced = _produced_queues()
    consumed = _consumed_queues()

    idle = {q for q in consumed if q not in produced} - set(_KNOWN_IDLE)
    assert not idle, (
        f"Workers listen on {sorted(idle)} but nothing enqueues there. Either fix the "
        "queue name, drop the worker, or record the reason in _KNOWN_IDLE."
    )


def test_outbound_queue_is_wired() -> None:
    """The specific regression: 855 ACKs and 856 ASNs must have a consumer."""
    assert "outbound" in _produced_queues()
    assert "outbound" in _consumed_queues(), "worker-outbound is missing from docker-compose.yml"
