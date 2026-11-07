"""Tesla site locale from the private profile or the local timezone.

The profile country wins. A timezone is the fallback. This does not read the
vehicle and it does not use a location fix.
"""

from __future__ import annotations

from pathlib import Path

from core.profile.owner import load_profile

# Official www.tesla.com locale segment. An empty string is the US site.
_COUNTRY = {
    "AU": "en_au",
    "GB": "en_gb",
    "NZ": "en_nz",
    "IE": "en_ie",
    "CA": "en_ca",
    "US": "",
    "DE": "de_de",
    "FR": "fr_fr",
    "NL": "nl_nl",
    "IT": "it_it",
    "ES": "es_es",
    "SE": "sv_se",
    "NO": "no_no",
    "DK": "da_dk",
    "FI": "fi_fi",
    "PT": "pt_pt",
    "JP": "ja_jp",
    "KR": "ko_kr",
    "SG": "en_sg",
    "AE": "en_ae",
    "MX": "es_mx",
}

_CANADA = {
    "America/Toronto",
    "America/Vancouver",
    "America/Edmonton",
    "America/Winnipeg",
    "America/Halifax",
    "America/St_Johns",
    "America/Regina",
    "America/Moncton",
    "America/Whitehorse",
    "America/Yellowknife",
    "America/Iqaluit",
}

_NAMES = {
    "": "the United States",
    "en_au": "Australia",
    "en_gb": "the United Kingdom",
    "en_nz": "New Zealand",
    "en_ie": "Ireland",
    "en_ca": "Canada",
    "de_de": "Germany",
    "fr_fr": "France",
    "nl_nl": "the Netherlands",
    "it_it": "Italy",
    "es_es": "Spain",
    "sv_se": "Sweden",
    "no_no": "Norway",
    "da_dk": "Denmark",
    "fi_fi": "Finland",
    "pt_pt": "Portugal",
    "ja_jp": "Japan",
    "ko_kr": "Korea",
    "en_sg": "Singapore",
    "en_ae": "the United Arab Emirates",
    "es_mx": "Mexico",
}


def locale_for(home: Path, tz) -> str:
    """Return the Tesla locale segment, or '' for https://www.tesla.com."""
    country = _profile_country(home)
    if country in _COUNTRY:
        return _COUNTRY[country]
    return _from_timezone(getattr(tz, "key", "") or "")


def locale_name(locale: str) -> str:
    return _NAMES.get(locale, "your region")


def site_root(locale: str) -> str:
    if locale:
        return f"https://www.tesla.com/{locale}"
    return "https://www.tesla.com"


def _profile_country(home: Path) -> str:
    path = Path(home) / "profile.yaml"
    if not path.is_file():
        return ""
    try:
        return load_profile(path).country
    except Exception:
        return ""


def _from_timezone(name: str) -> str:
    if name.startswith("Australia/"):
        return "en_au"
    if name in {"Pacific/Auckland", "Pacific/Chatham"}:
        return "en_nz"
    if name == "Europe/London":
        return "en_gb"
    if name == "Europe/Dublin":
        return "en_ie"
    if name in _CANADA:
        return "en_ca"
    if name == "Europe/Paris":
        return "fr_fr"
    if name == "Europe/Berlin":
        return "de_de"
    if name == "Europe/Amsterdam":
        return "nl_nl"
    return ""
