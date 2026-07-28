"""The optional direct-call path.

Everything else in harness-tuner runs on the standard library. This module is
the single exception, and it is written so that its dependency being absent is
a clear message rather than a crash somewhere unrelated.

Most people should not use it. The delegated path costs one extra round trip
through the agent that is already sitting there, and in exchange the engine
holds no credentials, contains no provider-specific code to go stale, and works
against any vendor including a local server. This module exists because some
people want one command instead of a conversation, and that is a reasonable
thing to want.

It speaks the OpenAI-compatible chat completions shape, which is what nearly
every hosted and local server exposes, including llama.cpp, vLLM, Ollama and
LM Studio. Point ``cost.local_endpoint`` at your server and it will be used;
leave it empty and the vendor default applies.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT_S = 120


class DirectCallUnavailable(RuntimeError):
    """The direct path was requested but cannot run. Says exactly why."""


def is_enabled(cfg) -> bool:
    return cfg.get("models.call_mode") == "direct"


def _endpoint(cfg) -> str:
    base = cfg.get("cost.local_endpoint") or os.environ.get("HARNESS_TUNER_ENDPOINT", "")
    if not base:
        raise DirectCallUnavailable(
            "models.call_mode is 'direct' but no endpoint is configured. Set "
            "cost.local_endpoint to your model server's base URL, or set "
            "models.call_mode back to 'delegated' and let your agent make the calls."
        )
    return base.rstrip("/") + "/chat/completions"


def preflight(cfg) -> list[str]:
    """Problems that would stop a direct call, reported before anything is spent."""
    problems: list[str] = []
    if not is_enabled(cfg):
        return problems
    if not (cfg.get("cost.local_endpoint") or os.environ.get("HARNESS_TUNER_ENDPOINT")):
        problems.append(
            "models.call_mode is 'direct' but cost.local_endpoint is empty and "
            "HARNESS_TUNER_ENDPOINT is unset, so no call can be made."
        )
    if not os.environ.get("HARNESS_TUNER_API_KEY"):
        problems.append(
            "models.call_mode is 'direct' but HARNESS_TUNER_API_KEY is unset. A local server "
            "that needs no key can be given any placeholder value."
        )
    return problems


def complete(cfg, *, model: str, system: str, user: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> str:
    """One chat completion. Raises with an actionable message on any failure."""
    if not is_enabled(cfg):
        raise DirectCallUnavailable(
            "the direct path was called while models.call_mode is 'delegated'. This is a bug "
            "in the caller, not a configuration problem."
        )
    url = _endpoint(cfg)
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
    ).encode("utf-8")

    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    key = os.environ.get("HARNESS_TUNER_API_KEY")
    if key:
        request.add_header("Authorization", f"Bearer {key}")

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise DirectCallUnavailable(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise DirectCallUnavailable(
            f"cannot reach {url}: {exc.reason}. If this is a local server, check it is running."
        ) from exc

    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DirectCallUnavailable(
            f"{url} answered in an unexpected shape: {json.dumps(payload)[:400]}"
        ) from exc


def describe(cfg) -> dict[str, Any]:
    """How this install makes model calls, for the doctor report."""
    mode = cfg.get("models.call_mode")
    if mode != "direct":
        return {
            "mode": "delegated",
            "engine_holds_credentials": False,
            "note": (
                "the engine calls no model. Your agent answers judge-request.json and "
                "diagnosis-request.json using whatever provider you already use."
            ),
        }
    return {
        "mode": "direct",
        "engine_holds_credentials": True,
        "endpoint": cfg.get("cost.local_endpoint") or "(vendor default)",
        "problems": preflight(cfg),
        "note": "the engine calls the model itself. This is the only path with a dependency.",
    }
