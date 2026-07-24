"""Tests for approval explanation rendering modes (plain vs. verbose)."""

import json
import pytest


def test_render_approval_explanation_plain_uses_content_when_available() -> None:
    """Plain mode should use the human-readable content when available."""
    from scripts.run_convobox import _render_approval_explanation_plain

    content = "Codex wants to: create file 'main.py'"
    result = _render_approval_explanation_plain(content, None, None)
    assert result == content


def test_render_approval_explanation_plain_extracts_file_path() -> None:
    """Plain mode should extract file paths from Claude Code tool_input."""
    from scripts.run_convobox import _render_approval_explanation_plain

    tool_input = json.dumps({"path": "/home/user/main.py", "content": "print('hello')"})
    result = _render_approval_explanation_plain(None, "write_text_file", tool_input)
    assert "main.py" in result or "/home/user/main.py" in result
    assert "Create or edit" in result


def test_render_approval_explanation_plain_extracts_command() -> None:
    """Plain mode should extract command from bash tool_input."""
    from scripts.run_convobox import _render_approval_explanation_plain

    tool_input = json.dumps({"command": "rm -rf /important/data"})
    result = _render_approval_explanation_plain(None, "bash", tool_input)
    assert "rm -rf" in result or "Run:" in result


def test_render_approval_explanation_plain_fallback_on_invalid_json() -> None:
    """Plain mode should gracefully fall back when JSON is invalid."""
    from scripts.run_convobox import _render_approval_explanation_plain

    invalid_json = "not valid json at all"
    result = _render_approval_explanation_plain(None, "bash", invalid_json)
    # Should not crash, should return something
    assert result is not None
    assert len(result) > 0


def test_render_approval_explanation_plain_fallback_when_no_data() -> None:
    """Plain mode should return a sensible fallback when no data is available."""
    from scripts.run_convobox import _render_approval_explanation_plain

    result = _render_approval_explanation_plain(None, None, None)
    assert "No further detail" in result


def test_render_approval_explanation_verbose_uses_raw_json() -> None:
    """Verbose mode should show the raw tool_input JSON."""
    from scripts.run_convobox import _render_approval_explanation_verbose

    tool = "bash"
    tool_input = '{"command": "ls -la"}'
    result = _render_approval_explanation_verbose(None, tool, tool_input)
    assert "with input:" in result
    assert tool_input in result or "ls -la" in result


def test_render_approval_explanation_verbose_uses_content_when_available() -> None:
    """Verbose mode should use content when available."""
    from scripts.run_convobox import _render_approval_explanation_verbose

    content = "Codex wants to: create file 'main.py'"
    result = _render_approval_explanation_verbose(content, None, None)
    assert result == content


def test_render_approval_explanation_dispatches_to_correct_renderer() -> None:
    """Main function should dispatch to correct renderer based on mode."""
    from scripts.run_convobox import _render_approval_explanation

    tool_input = json.dumps({"command": "make test"})

    # Test plain mode
    plain_result = _render_approval_explanation(
        None, "bash", tool_input, explanation_mode="plain"
    )
    assert "Run:" in plain_result or "make test" in plain_result
    assert "with input:" not in plain_result  # Should not show raw JSON

    # Test verbose mode
    verbose_result = _render_approval_explanation(
        None, "bash", tool_input, explanation_mode="verbose"
    )
    assert "with input:" in verbose_result


def test_render_approval_explanation_defaults_to_plain() -> None:
    """Main function should default to plain mode."""
    from scripts.run_convobox import _render_approval_explanation

    tool_input = json.dumps({"path": "/tmp/test.py"})
    result = _render_approval_explanation(None, "write_text_file", tool_input)
    # Should be human-friendly by default
    assert "Create or edit" in result or "/tmp/test.py" in result
