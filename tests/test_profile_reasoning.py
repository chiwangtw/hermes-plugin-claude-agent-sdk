"""The profile turns Hermes' reasoning_config into the ``reasoning_effort`` kwarg the client accepts."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("reasoning_config, expected", [
    ({"effort": "ultra"}, "max"),
    ({"effort": "max"}, "max"),
    ({"effort": "xhigh"}, "xhigh"),
    ({"effort": "high"}, "high"),
    ({"effort": "medium"}, "medium"),
    ({"effort": "low"}, "low"),
    ({"effort": "minimal"}, "low"),
    ({"effort": "none"}, "none"),
    ({"enabled": False, "effort": "high"}, "none"),
])
def test_effort_ladder_maps_to_sdk_vocabulary(profile, reasoning_config, expected):
    extra_body, top_level = profile.build_api_kwargs_extras(reasoning_config=reasoning_config)
    assert extra_body == {}
    assert top_level == {"reasoning_effort": expected}


def test_no_reasoning_config_sends_nothing(profile):
    assert profile.build_api_kwargs_extras(reasoning_config=None) == ({}, {})


def test_declares_full_hermes_ladder(profile):
    assert profile.supported_reasoning_efforts("claude-sonnet-5") == (
        "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
