"""H4: parse_stream_json_output must extract the result envelope and tool_use
calls from real `claude --output-format stream-json --verbose` output.

The fixtures are REAL captures from the installed claude CLI (v2.1.x):
  - stream_textonly.ndjson: a text-only answer (no tools)
  - stream_tooluse.ndjson:  a run that calls the Bash tool once
Regenerate by re-running the capture commands if the CLI format changes.
"""
from pathlib import Path

import pytest

from src.agents.trace import parse_stream_json_output

FIX = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIX / name).read_text()


def test_textonly_envelope_and_no_tools():
    envelope, tools = parse_stream_json_output(_read("stream_textonly.ndjson"))
    assert envelope is not None
    # result line has the same shape the rest of run_agent relies on
    assert envelope.get("type") == "result"
    assert envelope.get("is_error") is False
    assert envelope.get("result") == "hello"
    assert "num_turns" in envelope and "permission_denials" in envelope
    assert tools == []


def test_tooluse_extracts_tool_calls():
    envelope, tools = parse_stream_json_output(_read("stream_tooluse.ndjson"))
    assert envelope is not None and envelope.get("type") == "result"
    names = [t.tool for t in tools]
    assert "Bash" in names, names
    # input is summarized, not dropped
    bash = next(t for t in tools if t.tool == "Bash")
    assert "echo hi" in bash.input_summary


def test_fallback_to_single_json_envelope():
    """If no result line (old --output-format json), fall back to single-object."""
    envelope, tools = parse_stream_json_output(
        '{"type": "result", "is_error": false, "result": "x", "num_turns": 1}'
    )
    # one line that happens to be a result -> parsed via the line loop
    assert envelope["result"] == "x"
    assert tools == []

    # genuinely single-json with no per-line result type still parses via fallback
    envelope2, _ = parse_stream_json_output('{"is_error": false, "result": "y"}')
    assert envelope2["result"] == "y"


def test_garbage_and_blank_lines_are_ignored():
    envelope, tools = parse_stream_json_output("\n\nnot json\n\n")
    assert envelope is None
    assert tools == []
