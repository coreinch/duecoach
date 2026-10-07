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


def test_defaults_load(monkeypatch):
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("MORNING_HOUR", raising=False)
    assert importlib.reload(config).WEB_PORT == 8080
