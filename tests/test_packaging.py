"""What a Subscriber gets from ``hermes plugins install``: models, declared dependency, licence."""

from __future__ import annotations

import pytest
import yaml

from conftest import PLUGIN_ROOT, load_plugin_module

FALLBACK_MODELS = load_plugin_module().FALLBACK_MODELS


def test_curated_models_are_consistent(profile):
    models = profile.fallback_models
    assert models and len(set(models)) == len(models)
    assert all(m.startswith("claude-") for m in models)
    assert "claude-sonnet-5" in models, "the suggested default must be offered"
    assert profile.default_aux_model in models, "the aux model must be one the Runtime can run"
    assert "haiku" in profile.default_aux_model, "aux work should land on the cheapest tier"
    assert profile.fetch_models() is None, "no live catalog: Hermes must fall back to fallback_models"


@pytest.mark.parametrize("model", FALLBACK_MODELS)
def test_every_curated_model_runs_on_the_installed_runtime(client, model):
    # A model newer than the bundled Runtime is rejected by the API (issue #3): offering it is a lie.
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "Reply with the single word: pong"}], timeout=120)
    assert response.choices[0].message.content


def test_manifest_lets_hermes_install_the_sdk():
    manifest = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text())
    assert manifest["kind"] == "model-provider"
    assert manifest["name"] == profile_name_from_init()
    specs = manifest.get("python_dependencies") or []
    assert any(spec.startswith("claude-agent-sdk") for spec in specs)


def profile_name_from_init() -> str:
    import re
    return re.search(r'name="([^"]+)"', (PLUGIN_ROOT / "__init__.py").read_text()).group(1)


def test_mit_licence_present():
    assert (PLUGIN_ROOT / "LICENSE").read_text().splitlines()[0].strip() == "MIT License"
