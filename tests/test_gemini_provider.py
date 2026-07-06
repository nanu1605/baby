"""Gemini history sanitizing: foreign tool calls flatten to text (HTTP 400 fix).

Gemini 3 rejects replayed functionCall parts without a thought_signature —
every mid-loop router fallback carries exactly that shape (observed live:
'Function call is missing a thought_signature in functionCall parts').
"""

from __future__ import annotations

from core.providers.gemini import sanitize_history


def test_assistant_tool_calls_become_text():
    messages = [
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "web_search", "arguments": '{"query": "x"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"error": "denied"}'},
    ]
    out = sanitize_history(messages)
    assert out[0] == messages[0]
    assert out[1]["role"] == "assistant"
    assert "tool_calls" not in out[1]
    assert 'web_search({"query": "x"})' in out[1]["content"]
    assert out[2]["role"] == "user"
    assert "denied" in out[2]["content"]


def test_partial_text_before_tool_calls_is_kept():
    messages = [{"role": "assistant", "content": "Let me check.", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "get_time", "arguments": ""}}]}]
    out = sanitize_history(messages)
    assert out[0]["content"].startswith("Let me check.")
    assert "[called get_time({})]" in out[0]["content"]


def test_plain_messages_untouched():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert sanitize_history(messages) == messages
