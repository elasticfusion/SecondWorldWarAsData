"""Tests for src/utils/prompt_loader.py."""

# pylint: disable=missing-function-docstring

import pytest
import yaml

from src.utils.prompt_loader import (
    clear_cache,
    get_system_prompt,
    load_prompt,
    render_prompt,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear LRU cache between tests."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def prompts_dir(tmp_path, monkeypatch):
    """Create a temp prompts directory and patch LOCAL_PROMPTS_DIR."""
    import src.utils.prompt_loader as pl

    monkeypatch.setattr(pl, "LOCAL_PROMPTS_DIR", tmp_path)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    return tmp_path


class TestLoadPrompt:
    def test_loads_local_yaml(self, prompts_dir):
        data = {
            "prompt_template": "Extract {entity_type} from: {text}",
            "system_prompt": "You are an expert.",
            "schema": '{"type": "object"}',
        }
        (prompts_dir / "people.yaml").write_text(yaml.dump(data), encoding="utf-8")

        result = load_prompt("people")
        assert result["prompt_template"] == "Extract {entity_type} from: {text}"
        assert result["system_prompt"] == "You are an expert."

    def test_raises_on_missing(self, prompts_dir):
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            load_prompt("nonexistent")


class TestRenderPrompt:
    def test_renders_variables(self, prompts_dir):
        data = {"prompt_template": "Book: {book}\nText: {text}"}
        (prompts_dir / "test.yaml").write_text(yaml.dump(data), encoding="utf-8")

        result = render_prompt("test", book="Lorraine Campaign", text="Chapter 1")
        assert "Lorraine Campaign" in result
        assert "Chapter 1" in result

    def test_appends_rules(self, prompts_dir):
        data = {
            "prompt_template": "Extract entities from: {text}",
            "rules": ["Use ULIDs", "Be precise"],
        }
        (prompts_dir / "rules.yaml").write_text(yaml.dump(data), encoding="utf-8")

        result = render_prompt("rules", text="some text")
        assert "- Use ULIDs" in result
        assert "- Be precise" in result

    def test_injects_schema(self, prompts_dir):
        data = {
            "prompt_template": "Schema: {schema}\nText: {text}",
            "schema": '{"type": "object"}',
        }
        (prompts_dir / "schema.yaml").write_text(yaml.dump(data), encoding="utf-8")

        result = render_prompt("schema", text="data")
        assert '{"type": "object"}' in result


class TestGetSystemPrompt:
    def test_returns_system_prompt(self, prompts_dir):
        data = {"prompt_template": "x", "system_prompt": "You are helpful."}
        (prompts_dir / "sp.yaml").write_text(yaml.dump(data), encoding="utf-8")

        assert get_system_prompt("sp") == "You are helpful."

    def test_returns_none_if_missing(self, prompts_dir):
        data = {"prompt_template": "x"}
        (prompts_dir / "no_sp.yaml").write_text(yaml.dump(data), encoding="utf-8")

        assert get_system_prompt("no_sp") is None
