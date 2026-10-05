"""Optional personal context is carried without replacing default instructions."""

from custom_components.voice_satellite.external_transport.protocol import SessionStart


def test_prompt_append_is_omitted_for_existing_clients():
    message = SessionStart("session", "assist_satellite.fixture", "Fixture").as_message()
    assert "prompt_append" not in message["conversation"]


def test_prompt_append_is_serialized_independently_of_override():
    message = SessionStart(
        "session", "assist_satellite.fixture", "Fixture",
        initial_prompt="Existing override", prompt_append="Personal context",
    ).as_message()
    assert message["conversation"]["initial_prompt"] == "Existing override"
    assert message["conversation"]["prompt_append"] == "Personal context"
