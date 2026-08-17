"""Localization helpers for the GTK app."""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

DOMAIN = "proton-vpn-gtk-app"
LOCALE_DIR = Path(__file__).parent / "locale"

_translator: gettext.NullTranslations | gettext.GNUTranslations = gettext.NullTranslations()
RTL_LANGUAGES = {"ar", "dv", "fa", "ku", "ps", "ur", "yi"}


def initialize_i18n() -> None:
    """Initialize gettext using the user's system locale."""
    global _translator

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        # Keep default C locale if the host locale is not installed.
        pass

    _translator = gettext.translation(
        DOMAIN,
        localedir=str(LOCALE_DIR),
        fallback=True,
    )


def tr(message: str) -> str:
    """Translate a message using the currently loaded gettext catalog."""
    return _translator.gettext(message)


def should_use_rtl_layout() -> bool:
    """Return True if the current language should use an RTL UI layout."""
    language_code = _get_language_code()
    return language_code in RTL_LANGUAGES


def _get_language_code() -> str:
    """Resolve active language code from env/locale/gettext metadata."""
    for env_name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(env_name)
        parsed = _parse_language(value)
        if parsed:
            return parsed

    for category in (locale.LC_MESSAGES, locale.LC_CTYPE):
        lang, _encoding = locale.getlocale(category)
        parsed = _parse_language(lang)
        if parsed:
            return parsed

    parsed = _parse_language(_translator.info().get("language"))
    if parsed:
        return parsed

    return "en"


def _parse_language(value: str | None) -> str | None:
    if not value:
        return None

    primary = value.split(":", maxsplit=1)[0]
    normalized = primary.split(".", maxsplit=1)[0].split("@", maxsplit=1)[0]
    code = normalized.split("_", maxsplit=1)[0].strip().lower()
    if not code or code in {"c", "posix"}:
        return None
    return code
