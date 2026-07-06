"""The strict gate that keeps DB debris out of provider calls (P2, feature #7).

Failed or interrupted turns leave orphaned tool rows, malformed tool-call
arguments and empty-content rows in baby.db. The context loader used to replay
that verbatim, and strict providers 400 on it. ``sanitize_messages`` is applied
to the assembled messages array before it reaches a provider, so whatever the
DB (or a half-written turn) holds, the wire payload is always OpenAI-valid:

  * a ``tool`` message must follow the assistant ``tool_calls`` it answers,
    matched by id, in one contiguous call→responses block — orphans and
    id-mismatches drop;
  * assistant ``tool_calls`` with unparseable JSON arguments are repaired to
    ``"{}"`` (the model's intent is lost either way; a valid empty object keeps
    the sequence wire-legal);
  * assistant ``tool_calls`` left entirely unanswered are stripped (kept only
    if the same message still carries real text content);
  * empty / whitespace-only content drops, unless the row is an assistant
    carrying ``tool_calls`` (whose ``content`` is legitimately ``None``).

It is idempotent and a no-op on an already-valid sequence, including the normal
multi-tool-call turn the agent builds in-loop.
"""

from __future__ import annotations

import json
from typing import Any

_ROLES = ("system", "user", "assistant", "tool")


def _valid_json(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        json.loads(value)
        return True
    except (ValueError, TypeError):
        return False


def _has_content(message: dict) -> bool:
    content = message.get("content")
    return content is not None and str(content).strip() != ""


def sanitize_messages(messages: list[dict], report: list[dict] | None = None) -> list[dict]:
    """Return an OpenAI-valid copy of ``messages`` with poison excluded.

    When ``report`` is given, one dict per dropped row is appended to it so the
    caller can audit what the sanitizer removed.
    """

    def drop(message: dict, why: str) -> None:
        if report is not None:
            report.append(
                {
                    "why": why,
                    "role": message.get("role"),
                    "tool_call_id": message.get("tool_call_id"),
                }
            )

    # -- pass 1: normalise rows, repair tool-call args, drop empties ----------
    staged: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role not in _ROLES:
            drop(message, "unknown role")
            continue
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = dict(call.get("function") or {})
                if not _valid_json(function.get("arguments")):
                    function["arguments"] = "{}"
                calls.append({**call, "function": function})
            staged.append({**message, "tool_calls": calls})
        elif role == "tool":
            if not _has_content(message):
                drop(message, "empty tool content")
                continue
            staged.append(dict(message))
        else:  # system / user / assistant-without-tools
            if not _has_content(message):
                drop(message, "empty content")
                continue
            staged.append(dict(message))

    # -- pass 2: enforce assistant tool_calls <-> tool response pairing -------
    out: list[dict] = []
    i, n = 0, len(staged)
    while i < n:
        message = staged[i]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            # The contiguous run of tool rows immediately after is this call's
            # answer set; first answer per id wins.
            j = i + 1
            answers: dict[str | None, dict] = {}
            while j < n and staged[j].get("role") == "tool":
                tid = staged[j].get("tool_call_id")
                answers.setdefault(tid, staged[j])
                j += 1
            kept_calls = [c for c in message["tool_calls"] if c.get("id") in answers]
            if not kept_calls:
                # No answered calls: the tool turn is unusable. Keep the message
                # only if it still carries real text; drop stray answers.
                if _has_content(message):
                    out.append({k: v for k, v in message.items() if k != "tool_calls"})
                else:
                    drop(message, "assistant tool_calls all unanswered")
                for answer in staged[i + 1 : j]:
                    drop(answer, "orphaned tool answer")
                i = j
                continue
            out.append({**message, "tool_calls": kept_calls})
            kept_ids = {c.get("id") for c in kept_calls}
            for answer in staged[i + 1 : j]:
                tid = answer.get("tool_call_id")
                if tid in kept_ids and answers.get(tid) is answer:
                    out.append(answer)
                else:
                    drop(answer, "orphaned/mismatched tool answer")
            i = j
        elif message.get("role") == "tool":
            # A tool row not consumed by a preceding assistant run is an orphan.
            drop(message, "orphaned tool row")
            i += 1
        else:
            out.append(message)
            i += 1
    return out
