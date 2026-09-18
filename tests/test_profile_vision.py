"""Hermes only sends images (user messages and tool results) to providers that declare vision."""


def test_profile_declares_vision_including_tool_results(profile):
    assert profile.supports_vision is True
    assert profile.supports_vision_tool_messages is True
