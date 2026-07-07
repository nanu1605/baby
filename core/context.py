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


def estimate_tokens(messages: list[dict]) -> int:
    """Rough chars→tokens over message content; only ever feeds a threshold."""
    total = sum(len(str(m.get("content") or "")) for m in messages)
    return total // 4


def _blocks(indexed: list[tuple[int, dict]]) -> list[list[tuple[int, dict]]]:
    """Group non-system messages into atomic blocks. An assistant message that
    carries tool_calls binds the contiguous tool responses that answer it into
    one block, so trimming can never split a tool_call/tool_result pair."""
    blocks: list[list[tuple[int, dict]]] = []
    i, n = 0, len(indexed)
    while i < n:
        _, msg = indexed[i]
        group = [indexed[i]]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            j = i + 1
            while j < n and indexed[j][1].get("role") == "tool":
                group.append(indexed[j])
                j += 1
            i = j
        else:
            i += 1
        blocks.append(group)
    return blocks


def trim(messages: list[dict], budget: int | None) -> list[dict]:
    """Drop the oldest conversational turns until history fits ``budget`` tokens.

    Runs at each provider dispatch (per-brain budget). Inviolable: every
    ``system`` message (head prompt, rolling summary, RAG block, trailing
    nudge) is kept wherever it sits — ``budget`` covers conversational history
    only. The CURRENT turn is pinned whole: everything from the last user
    message to the end (its user question plus any in-flight tool rounds)
    survives even when it alone exceeds the budget, so the model never loses
    what was asked. Older turns drop oldest-first; a tool_call/tool_result pair
    is never split. ``budget`` of 0/None is a no-op (the engine: v1 rollback).
    """
    if not budget or budget <= 0:
        return list(messages)
    msgs = list(messages)
    non_sys = [(i, m) for i, m in enumerate(msgs) if m.get("role") != "system"]
    if estimate_tokens([m for _, m in non_sys]) <= budget:
        return msgs
    # Pin the current turn: from the last user message to the end. Only the
    # history BEFORE it is droppable (the trailing system nudge sits after the
    # user message but is a system row, so it is already pinned separately).
    last_user = next(
        (k for k in range(len(non_sys) - 1, -1, -1) if non_sys[k][1].get("role") == "user"),
        None,
    )
    droppable = non_sys if last_user is None else non_sys[:last_user]
    remaining = estimate_tokens([m for _, m in non_sys])
    drop: set[int] = set()
    for group in _blocks(droppable):  # oldest first
        if remaining <= budget:
            break
        remaining -= estimate_tokens([m for _, m in group])
        drop.update(idx for idx, _ in group)
    return [m for i, m in enumerate(msgs) if i not in drop]


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
