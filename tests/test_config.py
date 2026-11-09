import importlib

import pytest

from duecoach import config


def test_a_mistyped_number_names_the_setting(monkeypatch):
    monkeypatch.setenv("WEB_PORT", "eighty")
    with pytest.raises(SystemExit, match="WEB_PORT must be a number"):
        importlib.reload(config)


def test_an_out_of_range_number_is_refused(monkeypatch):
    monkeypatch.setenv("MORNING_HOUR", "25")
    with pytest.raises(SystemExit, match="MORNING_HOUR must be between"):
        importlib.reload(config)


def test_a_one_sided_limit_names_only_that_side(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "0")
    with pytest.raises(SystemExit, match=r"LLM_TIMEOUT must be at least 1, got 0\.0$"):
        importlib.reload(config)


def test_a_non_finite_number_is_refused(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "nan")
    with pytest.raises(SystemExit, match="LLM_TIMEOUT must be a finite number, got 'nan'"):
        importlib.reload(config)


def test_backoff_rejects_a_blank_list_or_a_step_that_is_not_a_positive_number(monkeypatch):
    monkeypatch.setenv("BACKOFF_MINUTES", "  , ")
    with pytest.raises(SystemExit, match="BACKOFF_MINUTES must be comma-separated numbers"):
        importlib.reload(config)
    monkeypatch.setenv("BACKOFF_MINUTES", "30,nan")
    with pytest.raises(SystemExit, match="BACKOFF_MINUTES must be positive numbers, got 'nan'"):
        importlib.reload(config)
    monkeypatch.setenv("BACKOFF_MINUTES", "0,60")
    with pytest.raises(SystemExit, match="BACKOFF_MINUTES must be positive numbers, got '0'"):
        importlib.reload(config)


def test_defaults_load(monkeypatch):
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("MORNING_HOUR", raising=False)
    monkeypatch.delenv("LLM_TIMEOUT", raising=False)
    monkeypatch.delenv("BACKOFF_MINUTES", raising=False)
    loaded = importlib.reload(config)
    assert loaded.WEB_PORT == 8080
    assert loaded.LLM_TIMEOUT == 60
    assert loaded.BACKOFF_MINUTES[0] == 30 and all(step > 0 for step in loaded.BACKOFF_MINUTES)
