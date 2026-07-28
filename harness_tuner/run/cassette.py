"""Cassettes: record a driven run once, then replay it free and deterministically.

This is what makes the tool usable more than once. A driven run costs real
money and produces different numbers every time, which is enough friction that
most evaluation tooling gets installed, run once, and never run again. A
cassette turns the second and every later run into a local file read.

Replay is not an approximation of the original run. The recorded payload is the
adapter's own intake record, so replaying produces the same trace, and
therefore the same metrics, byte for byte. That property is what lets the
conformance suite, CI, and a contributor on a laptop with no API key all
reproduce a number somebody else measured.

The cache key deliberately includes the config digest. Changing the model, the
adapter, or the containment answer invalidates the recording, because a
recording made under different conditions is a different experiment.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..protocol import canonical_json, digest


class Cassette:
    """A keyed store of recorded task outcomes, held as JSON lines."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.entries: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        self.writes = 0
        if os.path.isfile(self.path):
            self._load()

    def _load(self) -> None:
        with open(self.path, "r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{self.path}:{lineno}: corrupt cassette: {exc}") from exc
                self.entries[row["key"]] = row["payload"]

    @staticmethod
    def key(task_id: str, seed: int, config_digest: str) -> str:
        """Identity of one recorded task outcome."""
        return digest({"task_id": task_id, "seed": seed, "config": config_digest})

    def get(self, key: str) -> dict[str, Any] | None:
        payload = self.entries.get(key)
        if payload is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(canonical_json(payload))

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.entries[key] = payload
        self.writes += 1

    def save(self) -> None:
        """Write the cassette deterministically, sorted by key."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            for key in sorted(self.entries):
                handle.write(canonical_json({"key": key, "payload": self.entries[key]}))
                handle.write("\n")

    def content_digest(self) -> str:
        """Digest of everything recorded, stamped into the run artifacts."""
        return digest({k: self.entries[k] for k in sorted(self.entries)})

    def state(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "entries": len(self.entries),
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "content_digest": self.content_digest(),
        }
