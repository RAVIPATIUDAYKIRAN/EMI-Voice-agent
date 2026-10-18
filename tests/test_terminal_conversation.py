from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.config.settings import settings
from app.models import Customer
from app.services import ai_service
from scripts import run_terminal_conversation as terminal


def _fake_call():
    return SimpleNamespace(
        call_id=72,
        customer_id=2,
        customer_name="Test Customer",
        emi_id=8,
        emi_amount=6200.0,
        due_date="2026-10-25",
        emi_status="PENDING",
    )


def _response(reply, *, status="NOT_CONFIRMED", call_status="INITIATED"):
    return SimpleNamespace(
        agent_reply=reply,
        commitment_status=status,
        call_status=call_status,
    )


def _enable_ai(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("unit-test-key"))


def _read(values):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


@pytest.mark.anyio
async def test_call_setup_uses_existing_route_in_simulated_mode(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock())
    result = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(preferred_language="Hindi"))
    db.execute.return_value = result
    db.rollback = AsyncMock()
    # A real async context manager is needed by the helper's `async with`.
    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    start = AsyncMock(return_value=_fake_call())
    monkeypatch.setattr(terminal, "AsyncSessionLocal", lambda: SessionContext())
    monkeypatch.setattr(terminal, "start_simulated_call", start)

    call, language = await terminal._create_simulated_call(2)

    start.assert_awaited_once_with(customer_id=2, channel="simulated", db=db)
    db.execute.assert_awaited_once()
    assert db.execute.await_args.args[0].column_descriptions[0]["entity"] is Customer
    db.rollback.assert_awaited_once()
    assert call.call_id == 72
    assert language == "Hindi"


@pytest.mark.anyio
async def test_opening_turn_uses_existing_configured_responses_model(monkeypatch):
    _enable_ai(monkeypatch)
    response = SimpleNamespace(output_text="Hello, may I ask why your EMI is delayed?")
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(
        ai_service,
        "client",
        SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    reply = await ai_service.generate_agent_reply(
        customer_name="Sample Customer",
        emi_amount="6200.00",
        due_date="2026-10-25",
        conversation_history=[],
        preferred_language="English",
        opening_turn=True,
        emi_status="PENDING",
    )

    assert reply == response.output_text
    request = create.await_args.kwargs
    assert request["model"] == settings.openai_model
    assert isinstance(request["input"], str)
    assert request["input"].strip()
    assert "opening message" in request["input"]
    assert "opening turn" in request["instructions"]
    assert "PENDING" in request["instructions"]


@pytest.mark.anyio
async def test_multi_turn_uses_existing_conversation_history(monkeypatch):
    _enable_ai(monkeypatch)
    response = SimpleNamespace(output_text="Would you be able to pay on that date?")
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(
        ai_service,
        "client",
        SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    history = [
        {"role": "assistant", "content": "Why is your EMI delayed?"},
        {"role": "user", "content": "मेरी सैलरी अभी नहीं आई है।"},
    ]

    reply = await ai_service.generate_agent_reply(
        customer_name="Sample Customer",
        emi_amount="6200.00",
        due_date="2026-10-25",
        conversation_history=history,
        preferred_language="Hindi",
    )

    assert reply == response.output_text
    assert create.await_args.kwargs["input"] == history


@pytest.mark.anyio
async def test_missing_non_opening_input_fails_before_api_request(monkeypatch):
    _enable_ai(monkeypatch)
    create = AsyncMock()
    monkeypatch.setattr(
        ai_service,
        "client",
        SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    with pytest.raises(RuntimeError, match="without conversation input"):
        await ai_service.generate_agent_reply(
            customer_name="Sample Customer",
            emi_amount="6200.00",
            due_date="2026-10-25",
            conversation_history=[],
        )

    create.assert_not_awaited()


@pytest.mark.anyio
async def test_interactive_flow_reuses_handler_and_finishes_only_after_confirmation(monkeypatch):
    _enable_ai(monkeypatch)
    started = AsyncMock(return_value=(_fake_call(), "Hindi"))
    opening_ai = AsyncMock(return_value="नमस्ते, आपकी EMI के बारे में बात करूँ?")
    store_opening = AsyncMock()
    messages = AsyncMock(side_effect=[
        _response("आपकी सैलरी कब आएगी?"),
        _response("क्या आप 25 अक्टूबर को ₹6,200 दे पाएँगे?"),
        _response("आपकी पुष्टि दर्ज की गई है।", status="CONFIRMED", call_status="COMPLETED"),
    ])
    summary = AsyncMock(return_value=SimpleNamespace(
        call_summary="Payment commitment status: CONFIRMED",
        payment_commitment_confirmed=True,
    ))
    monkeypatch.setattr(terminal, "_create_simulated_call", started)
    monkeypatch.setattr(terminal, "generate_agent_reply", opening_ai)
    monkeypatch.setattr(terminal, "_store_agent_message", store_opening)
    monkeypatch.setattr(terminal, "_submit_customer_message", messages)
    monkeypatch.setattr(terminal, "_load_summary", summary)
    output = []
    replies = [
        "My salary is delayed.",
        "It should arrive on 20 October; I can pay on 25 October.",
        "हाँ, मैं 25 अक्टूबर को ₹6200 की EMI देने की पुष्टि करता हूँ।",
    ]

    result = await terminal.run_terminal_conversation(
        2, input_fn=_read(replies), output_fn=output.append
    )

    assert result == 0
    started.assert_awaited_once_with(2)
    opening_ai.assert_awaited_once()
    assert opening_ai.await_args.kwargs["opening_turn"] is True
    assert opening_ai.await_args.kwargs["preferred_language"] == "Hindi"
    store_opening.assert_awaited_once_with(72, "नमस्ते, आपकी EMI के बारे में बात करूँ?")
    assert [call.args[:2] for call in messages.await_args_list] == [
        (72, "My salary is delayed."),
        (72, "It should arrive on 20 October; I can pay on 25 October."),
        (72, "हाँ, मैं 25 अक्टूबर को ₹6200 की EMI देने की पुष्टि करता हूँ।"),
    ]
    summary.assert_awaited_once_with(72)
    assert any("CONFIRMED" in line for line in output)
    assert any(line.startswith("Call summary:") for line in output)


@pytest.mark.anyio
async def test_exit_stops_after_opening_without_customer_message(monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(terminal, "_create_simulated_call", AsyncMock(return_value=(_fake_call(), "English")))
    monkeypatch.setattr(terminal, "generate_agent_reply", AsyncMock(return_value="Hello."))
    store = AsyncMock()
    messages = AsyncMock()
    monkeypatch.setattr(terminal, "_store_agent_message", store)
    monkeypatch.setattr(terminal, "_submit_customer_message", messages)
    output = []

    result = await terminal.run_terminal_conversation(
        2, input_fn=_read(["exit"]), output_fn=output.append
    )

    assert result == 0
    store.assert_awaited_once_with(72, "Hello.")
    messages.assert_not_awaited()
    assert any("Session ended" in line for line in output)


@pytest.mark.anyio
async def test_empty_customer_input_is_reprompted_and_not_sent(monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(terminal, "_create_simulated_call", AsyncMock(return_value=(_fake_call(), "English")))
    monkeypatch.setattr(terminal, "generate_agent_reply", AsyncMock(return_value="Hello."))
    monkeypatch.setattr(terminal, "_store_agent_message", AsyncMock())
    messages = AsyncMock(return_value=_response("Please tell me more."))
    monkeypatch.setattr(terminal, "_submit_customer_message", messages)
    output = []

    result = await terminal.run_terminal_conversation(
        2, input_fn=_read(["   ", "I need more time.", "exit"]), output_fn=output.append
    )

    assert result == 0
    messages.assert_awaited_once_with(72, "I need more time.")
    assert any("Please enter a response" in line for line in output)


@pytest.mark.anyio
async def test_message_error_is_reported_without_exposing_exception_details(monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(terminal, "_create_simulated_call", AsyncMock(return_value=(_fake_call(), "English")))
    monkeypatch.setattr(terminal, "generate_agent_reply", AsyncMock(return_value="Hello."))
    monkeypatch.setattr(terminal, "_store_agent_message", AsyncMock())
    messages = AsyncMock(side_effect=HTTPException(status_code=503, detail="database unavailable"))
    monkeypatch.setattr(terminal, "_submit_customer_message", messages)
    output = []

    result = await terminal.run_terminal_conversation(
        2, input_fn=_read(["hello", "exit"]), output_fn=output.append
    )

    assert result == 0
    assert any("Message was not processed" in line for line in output)
    assert not any("secret" in line.lower() for line in output)


@pytest.mark.anyio
async def test_opening_model_error_is_handled_without_printing_provider_error(monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(terminal, "_create_simulated_call", AsyncMock(return_value=(_fake_call(), "English")))
    monkeypatch.setattr(terminal, "generate_agent_reply", AsyncMock(side_effect=RuntimeError("secret provider detail")))
    output = []

    result = await terminal.run_terminal_conversation(
        2, input_fn=_read([]), output_fn=output.append
    )

    assert result == 1
    assert any("could not generate the opening" in line.lower() for line in output)
    assert not any("secret provider detail" in line for line in output)


@pytest.mark.anyio
async def test_invalid_customer_selection_and_empty_api_key_fail_before_call(monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
    start = AsyncMock()
    monkeypatch.setattr(terminal, "_create_simulated_call", start)
    output = []

    result = await terminal.run_terminal_conversation(
        input_fn=_read(["not-an-id", "exit"]), output_fn=output.append
    )
    assert result == 0
    assert any("positive customer ID" in line for line in output)
    start.assert_not_awaited()

    result = await terminal.run_terminal_conversation(
        2, input_fn=_read([]), output_fn=output.append
    )
    assert result == 2
    start.assert_not_awaited()
