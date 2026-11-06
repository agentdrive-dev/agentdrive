import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "__pycache__", ".pytest_cache"}
PREREQS = [
    "vehicle-account.md",
    "vehicle-compatibility.md",
    "computer.md",
    "assistant.md",
    "developer-app.md",
    "billing.md",
    "public-key-host.md",
    "virtual-key.md",
    "time.md",
]


def _files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP for part in path.parts):
            continue
        if path.suffix in {".svg", ".png", ".pyc"}:
            continue
        if path.name == "test_hygiene_and_docs.py":
            continue
        yield path


def test_homepage_how_to_start_leads_with_the_paste_prompt():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    how_at = readme.index("## How to start")
    runs_at = readme.index("## What the assistant runs")
    assert how_at < readme.index("## How it works")
    assert how_at < readme.index("## What this version does")
    assert how_at < readme.index("## Illustrative example")
    assert readme.index("## Disclaimer") < runs_at < readme.index("## Supported agents")
    how = readme[how_at:readme.index("## How it works")]
    before_prompt = how.split("```text", 1)[0]
    for banned in ("git clone", "PYTHONPATH", "gate-present", "gate-accept", "setup/install.sh"):
        assert banned not in before_prompt
        assert banned not in how
    assert "<details>" not in how
    assert "```text" in how
    for required in (
        "DISCLAIMER.md",
        "SECURITY.md",
        "I have read, understood, and accept the AgentDrive disclaimer and security reminder",
        "What the assistant runs",
        "~/.config/agentdrive",
        "$AGENTDRIVE_HOME",
        "Same-turn acceptance is rejected",
        "will not create it",
    ):
        assert required in how
    technical = readme[runs_at:readme.index("## Supported agents")]
    assert "git clone https://github.com/agentdrive-dev/agentdrive.git ~/agentdrive" in technical
    assert "PYTHONPATH=. python3 -m core gate-present --turn-id 1" in technical
    assert 'gate-accept --turn-id 2 --statement "I have read, understood, and accept the AgentDrive disclaimer and security reminder"' in technical
    assert "bash setup/install.sh" in technical
    assert "Same-turn acceptance is rejected" in technical
    assert "will not create it" in technical
    for image in (
        "docs/images/how-it-works.svg",
        "docs/images/what-it-does.svg",
        "docs/images/ask-in-plain-words.svg",
    ):
        assert image in readme
    assert "sample data" in readme.lower()


def test_readme_next_lists_planned_work_in_positive_language():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Not in this version" not in readme
    assert "### Not in this version" not in readme
    next_at = readme.index("## Next")
    assert readme.index("## What this version does") < next_at < readme.index("## Illustrative example")
    section = readme[next_at:readme.index("## Illustrative example")]
    lowered = section.lower()
    for banned in ("no scheduled", "no where", "no automatic", "no trunk", "no china", "not in this version"):
        assert banned not in lowered
    assert "supercharger" not in lowered
    assert "china-region" not in lowered
    assert "charging timetable" in lowered
    assert "where" not in lowered
    assert "coarse area" not in lowered
    assert "morning report" in lowered
    assert "trunk" in lowered and "frunk" in lowered and "remote start" in lowered
    for skill in ("onboarding", "trip advisor", "charging tips", "news watch"):
        assert skill in lowered
    assert "unconfirmed against a live response" in lowered
    assert "revised design" in lowered
    commands = readme[readme.index("## What this version does"):next_at]
    assert "| `where`" in commands
    assert "agentdrive where" in commands
    assert "charge-schedule" not in commands
    assert "morning report" not in commands.lower()


def test_guides_and_checklist_links():
    checklist = (ROOT / "docs" / "00-before-you-start.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in PREREQS:
        page = ROOT / "docs" / "prerequisites" / name
        assert page.is_file(), name
        text = page.read_text(encoding="utf-8")
        assert "[Back to the checklist](../00-before-you-start.md)" in text
        assert f"prerequisites/{name}" in checklist
        assert f"docs/prerequisites/{name}" in readme
        for section in re.findall(r"(## Step [\s\S]*?)(?=\n## |\Z)", text):
            assert "../images/" in section and ".svg" in section
    for number in range(10):
        matches = list((ROOT / "docs").glob(f"{number:02d}-*.md"))
        assert len(matches) == 1
    assert (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8").startswith("Experimental and testing purposes only.")
    assert "to the maximum extent permitted by law" in readme.lower()
    assert "Australian Consumer Law" in readme


ALLOWED_EMAIL = "agentdrivedev@gmail.com"


def test_contact_email_is_the_only_published_address():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    contact = re.search(r"(?ms)^## Contact\n.*?(?=^## |\Z)", readme)
    assert contact, "README is missing a Contact section"
    assert contact.group(0).strip().endswith(ALLOWED_EMAIL)
    assert ALLOWED_EMAIL in security
    assert "https://github.com/agentdrive-dev/agentdrive/issues" in readme
    assert "https://github.com/agentdrive-dev/agentdrive/security/advisories/new" in security
    assert "https://agentdrive-dev.github.io/agentdrive" in readme


def test_repo_has_no_personal_data_or_secrets():
    banned = ("2" + "cz", "model y " + "l", "mel" + "bourne", "john" + "ny", "tesla." + "example.com", "agentdrive-" + "old")
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    cjk = re.compile(r"[\u3400-\u9fff]")
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        relative = path.relative_to(ROOT).as_posix()
        for word in banned:
            assert word not in lowered, relative
        assert "BEGIN " + "PRIVATE KEY" not in text, relative
        assert cjk.search(text) is None, relative
        for match in email.findall(text):
            assert match == ALLOWED_EMAIL, f"{relative} contains an address: {match}"
        if "itbird" in lowered:
            assert "github.com/agentdrive-dev/agentdrive" in lowered, relative
        if path.suffix == ".py" and "regions.yaml" not in relative:
            for host in ("fleet-api.prd", "developer.tesla.com", "auth.tesla.com", "fleet-auth", "tesla.cn"):
                assert host not in text, relative
