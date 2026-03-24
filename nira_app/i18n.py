import gettext
from pathlib import Path
from typing import Callable

LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def get_translator(lang_code: str) -> Callable[[str], str]:
    # Extract language code (e.g., 'en', 'fr', 'es')
    code = lang_code.split("-")[0].lower()

    try:
        # Try to load the translation for the specified language
        translation = gettext.translation("nira", localedir=str(LOCALES_DIR), languages=[code], fallback=True)

        # Wrap the method in a lambda so Jinja2 evaluates it correctly as a callable
        def _(text: str) -> str:
            return translation.gettext(text)

        return _
    except Exception:
        # Fallback to no-op translator if anything fails
        def _(text: str) -> str:
            return text

        return _
