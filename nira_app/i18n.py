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
        return translation.gettext
    except Exception:
        # Fallback to no-op translator if anything fails
        return lambda x: x
