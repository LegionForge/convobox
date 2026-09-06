"""Tests for ACPAdapter transport and request/response routing."""

import asyncio

import pytest

from convobox.adapters.acp import ACPAdapter
from convobox.adapters.base import BackendEvent, BackendEventType


@pytest.mark.asyncio
async def test_acp_adapter_imports():
    """Verify ACPAdapter can be imported."""
    assert ACPAdapter is not None


@pytest.mark.asyncio
async def test_acp_adapter_init():
    """Test ACPAdapter initialization with various commands."""
    # Test with default opencode command
    adapter = ACPAdapter()
    assert adapter._backend == "opencode"
    # Command can be either a bare name or a resolved path (with .exe on Windows)
    assert "opencode" in adapter._command[0].lower()
    assert "acp" in adapter._command

    # Test with explicit command
    adapter_explicit = ACPAdapter(
        command=["custom-acp-server"],
        backend="custom",
    )
    assert adapter_explicit._command == ["custom-acp-server"]
    assert adapter_explicit._backend == "custom"

    # Test with working_dir
    adapter_wd = ACPAdapter(working_dir="/tmp/test")
    assert adapter_wd._working_dir == "/tmp/test"


@pytest.mark.asyncio
async def test_acp_adapter_request_response_routing():
    """Test that responses are correctly routed to their requests."""
    adapter = ACPAdapter()

    # Simulate a mock backend by creating a simple echo subprocess
    # For now, just test the data structures
    assert adapter._pending == {}
    assert adapter._request_seq == 0

    # Manually test the request correlation logic
    request_id = adapter._request_seq
    adapter._request_seq += 1

    future = asyncio.Future()
    adapter._pending[request_id] = future

    # Simulate a response
    response_payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"status": "ok"},
    }

    # Process the response (this would normally happen in _read_loop)
    if "id" in response_payload and "result" in response_payload:
        request_id_from_response = response_payload["id"]
        if request_id_from_response in adapter._pending:
            adapter._pending[request_id_from_response].set_result(
                response_payload.get("result", {})
            )

    result = await future
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_acp_adapter_notification_processing():
    """Test that notifications are processed and converted to BackendEvents."""
    adapter = ACPAdapter()

    # Test TEXT event
    notification = {
        "type": "agent_message_chunk",
        "text": "Hello, world!",
    }

    await adapter._process_notification(notification)

    # The event should be queued
    event = adapter._events.get_nowait()
    assert isinstance(event, BackendEvent)
    assert event.type == BackendEventType.TEXT
    assert event.content == "Hello, world!"


@pytest.mark.asyncio
async def test_acp_adapter_is_busy():
    """Test the is_busy flag."""
    adapter = ACPAdapter()
    assert not adapter.is_busy()

    adapter._busy = True
    assert adapter.is_busy()

    adapter._busy = False
    assert not adapter.is_busy()
