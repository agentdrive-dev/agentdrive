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


INSTALL_URL = "https://agentdrive-dev.github.io/agentdrive/docs/00-before-you-start.html"
ACCEPTANCE = "I have read, understood, and accept the AgentDrive disclaimer and security reminder"


def test_homepage_how_to_start_leads_with_the_paste_prompt():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    how_at = readme.index("## How to start")
    disclaimer_at = readme.index("## Disclaimer")
    assert readme.index("## How it works") < readme.index("## What you can do") < how_at
    assert readme.index("## A conversation") < readme.index("## A morning report") < how_at
    assert how_at < disclaimer_at < readme.index("## Supported agents")
    how = readme[how_at:disclaimer_at]
    assert "<details>" not in how
    assert "```text" in how
    assert INSTALL_URL in how
    assert "docs/00-before-you-start.md" in how
    assert "follow them exactly" in how.lower()
    assert "Do not invent a second installer or its own steps." in how
    assert "Only ask me for the approvals those instructions say the human must do." in how
    assert "[**Open the install page**](docs/00-before-you-start.md)" in how
    assert "about an hour" in how.lower()
    for banned in (
        "git clone",
        "PYTHONPATH",
        "gate-present",
        "gate-accept",
        "setup/install.sh",
        "What the AI agent runs",
        "Get the code",
    ):
        assert banned not in how
    for banned in (
        "git clone",
        "gate-present",
        "TeslaMate",
        "teslamate",
        "trip advisor",
        "charging tips",
        "## Next",
        "## What this version does",
        "## Not in this version",
        "### Not in this version",
        "may wake a sleeping car",
        "| Command |",
    ):
        assert banned not in readme
    assert "**You just chat in plain words." in readme
    assert "The AI agent translates that into AgentDrive commands." in readme
    assert "You do not need to learn, memorize, or type the CLI commands." in readme
    assert "Grok Bot, Muse, or any shell-capable AI agent uses this same path." in readme
    assert "not an official tesla product" in readme.lower()
    for image in (
        "docs/images/how-it-works.svg",
        "docs/images/what-it-does.svg",
        "docs/images/ask-in-plain-words.svg",
    ):
        assert image in readme
    assert "sample data" in readme.lower()
    assert "not a vehicle reading or a transcript" in readme.lower()
    can_do = readme[readme.index("## What you can do"):readme.index("## A conversation")].lower()
    assert "rear trunk" in can_do
    assert "frunk" in can_do
    assert "remote start" in can_do
    assert "lock" in can_do
    assert "climate" in can_do
    assert "charging" in can_do
    assert "honk" in can_do
    assert "health alert" in can_do
    assert "morning briefing" in can_do


def test_install_page_holds_the_full_prompt_and_agent_steps():
    page = (ROOT / "docs" / "00-before-you-start.md").read_text(encoding="utf-8")
    how_at = page.index("## How to start")
    need_at = page.index("## What you need")
    will_at = page.index("## What you will do")
    runs_at = page.index("## What the AI agent runs")
    guide_at = page.index("## Full step-by-step guide")
    assert how_at < need_at < will_at < runs_at < guide_at
    how = page[how_at:need_at]
    prompt = how.split("```text", 1)[1].split("```", 1)[0]
    assert "in the README" not in prompt
    assert "docs/00-before-you-start.md" in prompt
    assert "The prompt is on that page in full" not in page
    assert "../README.md#what-the-ai-agent-runs" not in page
    assert "../README.md#next" not in page
    for required in (
        "DISCLAIMER.md",
        "SECURITY.md",
        ACCEPTANCE,
        "What the AI agent runs",
        "You just chat in plain words.",
        "You do not need to learn, memorize, or type the CLI commands.",
        "~/.config/agentdrive",
        "$AGENTDRIVE_HOME",
        "Do not invent a second installer.",
        "07:30",
        "agentdrive history due",
        "every 30 minutes",
        "agentdrive check --json",
        "agentdrive report",
        "Same-turn acceptance is rejected",
        "will not create it",
        "the previous week",
        "the previous month",
        "the previous year",
        "the previous day",
    ):
        assert required in how
    technical = page[runs_at:guide_at]
    assert "git clone https://github.com/agentdrive-dev/agentdrive.git ~/agentdrive" in technical
    assert "PYTHONPATH=. python3 -m core gate-present --turn-id 1" in technical
    assert f'gate-accept --turn-id 2 --statement "{ACCEPTANCE}"' in technical
    assert "bash setup/install.sh" in technical
    assert "Same-turn acceptance is rejected" in technical
    assert "will not create it" in technical
    assert "different `--turn-id`" in technical
    for number in range(1, 10):
        assert f"{number:02d}-" in page[guide_at:]


def test_homepage_does_not_list_unfinished_work():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Next" not in readme
    assert "## Not in this version" not in readme
    assert "### Not in this version" not in readme
    for path in [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]:
        text = path.read_text(encoding="utf-8")
        assert "Not in this version" not in text, path
        assert "README.md#next" not in text, path
    commands = (ROOT / "docs" / "06-queries-and-commands.md").read_text(encoding="utf-8")
    assert "| `where`" in commands
    assert "agentdrive where" in commands
    assert "charge-schedule show" in commands
    assert "charge-schedule set" in commands
    assert "charge-schedule remove" in commands
    assert "agentdrive report" in commands
    assert "report-schedule" in commands
    assert "history due" in commands
    assert "does not use the park check" in commands
    assert "SQLCipher is not required" in commands
    assert "stays quiet unless something needs attention" not in commands.lower()
    assert "| `trunk-open`" in commands
    assert "| `trunk-close`" in commands
    assert "| `frunk-open`" in commands
    assert "| `remote-start`" in commands
    assert "frunk-close" not in commands
    assert "close-frunk" not in commands
    assert "open rear trunk" in commands
    assert "close rear trunk" in commands
    assert "open frunk" in commands
    assert "enable remote start" in commands


def test_guides_and_checklist_links():
    checklist = (ROOT / "docs" / "00-before-you-start.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in PREREQS:
        page = ROOT / "docs" / "prerequisites" / name
        assert page.is_file(), name
        text = page.read_text(encoding="utf-8")
        assert "[Back to the checklist](../00-before-you-start.md)" in text
        assert f"prerequisites/{name}" in checklist
        for section in re.findall(r"(## Step [\s\S]*?)(?=\n## |\Z)", text):
            assert "../images/" in section and ".svg" in section
    for number in range(10):
        matches = list((ROOT / "docs").glob(f"{number:02d}-*.md"))
        assert len(matches) == 1
    assert (ROOT / "DISCLAIMER.md").read_text(encoding="utf-8").startswith("Experimental and testing purposes only.")
    assert "to the maximum extent permitted by law" in readme.lower()
    assert "Australian Consumer Law" in readme


ALLOWED_EMAIL = "agentdrivedev@gmail.com"


def test_pages_site_title_is_metadata_and_not_a_visible_header():
    config = (ROOT / "_config.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^theme:\s*jekyll-theme-primer\s*$", config)
    assert re.search(r"(?m)^title:\s*AgentDrive\s*$", config)
    layout = (ROOT / "_layouts" / "default.html").read_text(encoding="utf-8")
    assert "site.title" not in layout
    assert "{{ content }}" in layout
    assert "markdown-body" in layout
    assert "/assets/css/style.css" in layout
    assert "{% seo %}" in layout
    assert "This site is open source." in layout


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


def _without_fences(text: str) -> str:
    return re.sub(r"(?s)```.*?```", "", text)


def _heading_slugs(text: str) -> set[str]:
    counts = {}
    slugs = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        raw = re.sub(r"`([^`]*)`", r"\1", match.group(1).strip())
        raw = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw)
        slug = re.sub(r"[^\w\s-]", "", raw.lower(), flags=re.UNICODE)
        slug = re.sub(r"\s+", "-", slug)
        seen = counts.get(slug, 0)
        counts[slug] = seen + 1
        slugs.add(slug if seen == 0 else f"{slug}-{seen}")
    return slugs


def test_internal_markdown_links_resolve():
    pages = [ROOT / "README.md", ROOT / "DISCLAIMER.md", ROOT / "SECURITY.md", * (ROOT / "docs").rglob("*.md")]
    for path in pages:
        visible = _without_fences(path.read_text(encoding="utf-8"))
        for target in re.findall(r"\]\(([^)\s]+)\)", visible):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, sep, anchor = target.partition("#")
            if path_part:
                resolved = (path.parent / path_part).resolve()
                assert resolved.is_file(), f"{path.relative_to(ROOT)} -> {target}"
                body = resolved.read_text(encoding="utf-8")
            else:
                body = path.read_text(encoding="utf-8")
            if sep:
                assert anchor in _heading_slugs(body), f"{path.relative_to(ROOT)} -> #{anchor}"
