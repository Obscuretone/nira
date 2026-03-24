from nira_app.i18n import get_translator


def test_i18n_fallback():
    _ = get_translator("en")
    assert _("Ticket List") == "Ticket List"
    assert _("Unknown") == "Unknown"


def test_i18n_french():
    _ = get_translator("fr-FR")
    assert _("Ticket List") == "Liste des tickets"


def test_i18n_spanish():
    _ = get_translator("es")
    assert _("Ticket List") == "Lista de tickets"


def test_i18n_german():
    _ = get_translator("de")
    assert _("Ticket List") == "Ticketliste"


def test_missing_language():
    _ = get_translator("xyz")
    assert _("Title") == "Title"


def test_regional_language():
    _ = get_translator("es-MX")
    assert _("Title") == "Título"


def test_missing_language_code():
    _ = get_translator("zz")
    assert _("Hello") == "Hello"


def test_complex_region_code():
    _ = get_translator("fr-CA")
    assert _("Ticket List") == "Liste des tickets"
