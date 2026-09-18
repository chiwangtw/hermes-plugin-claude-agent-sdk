"""What a Subscriber gets from ``hermes plugins install``: models, declared dependency, licence."""

from __future__ import annotations

import yaml

from conftest import PLUGIN_ROOT

EXPECTED_MODELS = ("claude-sonnet-5", "claude-fable-5-1", "claude-opus-5", "claude-haiku-4-5-20251001")


def test_curated_models_and_aux_default(profile):
    assert profile.fallback_models == EXPECTED_MODELS
    assert profile.fetch_models() == list(EXPECTED_MODELS)
    assert profile.default_aux_model == "claude-haiku-4-5-20251001"


def test_manifest_declares_sdk_dependency():
    manifest = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text())
    assert manifest["name"] == "claude-agent-sdk"
    assert manifest["kind"] == "model-provider"
    assert manifest["version"] == "0.1.0"
    assert manifest["python_dependencies"] == ["claude-agent-sdk>=0.2.156"]


def test_mit_licence_present():
    assert (PLUGIN_ROOT / "LICENSE").read_text().splitlines()[0].strip() == "MIT License"
