import pytest

from adapters.detect import load_adapter, resolve_adapter
from adapters.grokbot import GrokBotAdapter
from adapters.muse import MuseAdapter
from adapters.template import TemplateAdapter
from core.cli import main
from core.errors import AmbiguousAdapter


def test_grok_and_cursor_signals_select_grokbot(tmp_path):
    choice = resolve_adapter(home=tmp_path, env={"CURSOR_AGENT": "1"})
    assert choice.adapter == "grokbot"
    assert choice.source == "detected"
    assert "CURSOR_AGENT" in choice.signals
    saved = (tmp_path / "host.yaml").read_text(encoding="utf-8")
    assert "adapter: grokbot" in saved
    assert isinstance(load_adapter("grokbot", root=tmp_path), GrokBotAdapter)


def test_muse_signal_selects_the_file_backed_adapter(tmp_path):
    choice = resolve_adapter(home=tmp_path, env={"MUSE_HOME": "/tmp/muse"})
    assert choice.adapter == "muse"
    adapter = load_adapter("muse", root=tmp_path)
    assert isinstance(adapter, MuseAdapter)
    assert adapter.root == tmp_path


def test_no_signal_is_unknown_and_uses_the_template(tmp_path):
    choice = resolve_adapter(home=tmp_path, env={})
    assert choice.adapter == "unknown"
    assert choice.source == "detected"
    assert isinstance(load_adapter("unknown", root=tmp_path), TemplateAdapter)


def test_conflicting_signals_ask_and_do_not_write(tmp_path):
    with pytest.raises(AmbiguousAdapter):
        resolve_adapter(home=tmp_path, env={"GROK_BOT": "1", "MUSE_AGENT": "1"})
    assert not (tmp_path / "host.yaml").exists()


def test_cli_override_beats_detection_and_sticks(tmp_path):
    first = resolve_adapter(home=tmp_path, env={"CURSOR_AGENT": "1"}, cli_adapter="template")
    assert first.adapter == "template"
    assert first.source == "override"
    second = resolve_adapter(home=tmp_path, env={"CURSOR_AGENT": "1"})
    assert second.adapter == "template"
    assert second.source == "override"


def test_disabled_values_are_not_signals(tmp_path):
    choice = resolve_adapter(home=tmp_path, env={"GROK_BOT": "0", "MUSE_BOT": "false"})
    assert choice.adapter == "unknown"


def test_cli_asks_when_the_process_matches_both_hosts(tmp_path, capsys, monkeypatch):
    for name in ("CURSOR_AGENT", "GROK_BOT", "GROKBOT", "GROK_BOT_HOME", "GROK_BOT_SESSION", "MUSE_BOT", "MUSE_HOME", "MUSE_SESSION", "MUSE_AGENT", "AGENTDRIVE_ADAPTER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CURSOR_AGENT", "1")
    monkeypatch.setenv("MUSE_HOME", "/tmp/muse")
    code = main(["--home", str(tmp_path), "adapter", "install"])
    assert code == 3
    assert not (tmp_path / "host.yaml").exists()
    assert "ambiguous" in capsys.readouterr().err.lower()
    code = main(["--home", str(tmp_path), "adapter", "--adapter", "template", "show"])
    assert code == 0
    assert "template (override)" in capsys.readouterr().out
