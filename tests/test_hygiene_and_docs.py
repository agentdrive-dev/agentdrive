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
    disclaimer = (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8")
    assert disclaimer.startswith("Experimental and testing purposes only.")
    assert "to the maximum extent permitted by law" in disclaimer.lower()
    assert "Australian Consumer Law" in disclaimer
    assert "[DISCLAIMER.md](DISCLAIMER.md)" in readme
    assert "[SECURITY.md](SECURITY.md)" in readme


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
