import json

import pytest

from adapters.grokbot import GrokBotAdapter
from adapters.muse import MuseAdapter
from adapters.template import TemplateAdapter
from core.errors import MemoryRejected, ProfileError, ScheduleBlocked
from core.profile.owner import load_profile, save_profile
from core.safety.confirm import Challenge


def prompter(challenge):
    return f"I confirm {challenge.command.replace('_', ' ')}"


def test_profile_round_trip_and_rejected_fields(tmp_path):
    source = load_profile("config/profile.example.yaml")
    path = tmp_path / "profile.yaml"
    save_profile(path, source)
    loaded = load_profile(path)
    assert loaded.timezone == "Etc/UTC"
    assert loaded.places[0].suburb == "Example Suburb"
    assert (path.stat().st_mode & 0o777) == 0o600

    bad = tmp_path / "bad.yaml"
    bad.write_text("country: US\ntimezone: Etc/UTC\nvin: ABCDEFG1234567890\n", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(bad)


def test_template_and_grokbot_refuse_secrets_and_sensitive_schedules(tmp_path):
    template = TemplateAdapter(prompter, secrets={"client_id": "abc"})
    with pytest.raises(MemoryRejected):
        template.remember("vin", "5YJ00000000000000")
    with pytest.raises(ScheduleBlocked):
        template.schedule("night", "22:00", {"command": "unlock"})
    template.schedule("briefing", "07:30", {"skill": "morning_briefing"})
    assert template.notify("all quiet", requires_action=False) is False
    assert template.notify("lock the vehicle", requires_action=True) is True

    grok = GrokBotAdapter(tmp_path, prompter)
    grok.remember("home", "Example Suburb")
    assert "home" in json.loads((tmp_path / "memory.json").read_text())
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "client-id").write_text("client-123\n", encoding="utf-8")
    assert grok.get_secret("client_id") == "client-123"
    assert "client-123" not in (tmp_path / "memory.json").read_text()

    challenge = Challenge("1", "unlock", "Confirm unlock")
    first = grok.confirm(challenge)
    second = grok.confirm(challenge)
    assert first.statement.startswith("I confirm")
    assert second.action_id == "1"
    grok._in_schedule = True
    with pytest.raises(ScheduleBlocked):
        grok.confirm(challenge)


def test_muse_adapter_matches_the_file_backed_rules(tmp_path):
    muse = MuseAdapter(tmp_path, prompter)
    with pytest.raises(MemoryRejected):
        muse.remember("vin", "5YJ00000000000000")
    with pytest.raises(ScheduleBlocked):
        muse.schedule("night", "22:00", {"command": "unlock"})
    muse.remember("home", "Example Suburb")
    assert "home" in json.loads((tmp_path / "memory.json").read_text())
    muse.schedule("health-check", "every 6 hours", {"command": "check", "json": True})
    assert muse.notify("all quiet", requires_action=False) is False
    assert muse.notify("tyre pressure is low", requires_action=True) is True
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "client-id").write_text("client-123\n", encoding="utf-8")
    assert muse.get_secret("client_id") == "client-123"
    assert "client-123" not in (tmp_path / "memory.json").read_text()
