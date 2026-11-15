import pytest


@pytest.fixture(autouse=True)
def _skip_command_tool_rebuild(monkeypatch):
    """Leave the car command tool alone unless a test opts back into the real check."""
    monkeypatch.setattr("core.command_tool.ensure_command_tool", lambda *args, **kwargs: None)
