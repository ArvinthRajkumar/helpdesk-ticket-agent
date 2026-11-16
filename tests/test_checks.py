"""Code checks on replies. Replies here are written for the tests, not taken from tickets."""

from ticket_agent.checks import code_checks
from ticket_agent.decide import load_faq
from ticket_agent.models import card_numbers, has_payment_details, luhn_ok

FAQ_TEXT, FAQ = load_faq()


def check(reply: str, cited: list[str], language: str = "nl") -> dict:
    return code_checks(reply, language, cited, FAQ, FAQ_TEXT)


GOOD_NL = (
    'Hoi, ga naar app.roosterly.nl/login en klik op "Wachtwoord vergeten". '
    "De link is 60 minuten geldig. Geen mail na 10 minuten? Kijk dan in je spam. Roosterly Support"
)


def test_grounded_dutch_reply_passes_every_code_check() -> None:
    assert check(GOOD_NL, ["F01"]) == {
        "language": None,
        "numbers": None,
        "paths": None,
        "payment": None,
    }


def test_language_mismatch_fails() -> None:
    english = "Hi, you can reset it from the login page and then check your spam. Roosterly Support"
    assert "detected as en" in check(english, ["F01"])["language"]


def test_number_not_in_the_cited_entry_fails() -> None:
    reply = GOOD_NL.replace("60 minuten", "24 uur")
    assert "24" in check(reply, ["F01"])["numbers"]


def test_number_from_an_uncited_entry_fails() -> None:
    reply = "Hoi, het Basis abonnement kost €4 per medewerker. Roosterly Support"
    assert check(reply, ["F01"])["numbers"] and check(reply, ["F05"])["numbers"] is None


def test_price_formats_are_the_same_number() -> None:
    reply = "Hoi, Plus kost €6,00 per actieve medewerker per maand. Roosterly Support"
    assert check(reply, ["F05"])["numbers"] is None


def test_menu_path_must_match_the_faq_step_by_step() -> None:
    ok = "Hoi, ga naar Instellingen → Koppelingen → Salaris om dit in te stellen. Roosterly Support"
    assert check(ok, ["F04"])["paths"] is None
    made_up = "Hoi, ga naar Instellingen → Export → Salaris om dit in te stellen. Roosterly Support"
    assert "Instellingen → Export" in check(made_up, ["F04"])["paths"]


def test_ascii_arrows_are_checked_too() -> None:
    reply = "Hoi, ga naar Instellingen > Facturatie om dit te doen. Roosterly Support"
    assert "Instellingen → Facturatie" in check(reply, ["F06"])["paths"]


def test_unknown_address_quote_or_email_fails() -> None:
    sign = " Roosterly Support"
    assert "address" in check("Hoi, kijk op help.roosterly.nl voor meer." + sign, ["F11"])["paths"]
    assert "quoted" in check('Hoi, klik op "Account herstellen" en dan.' + sign, ["F01"])["paths"]
    assert "email" in check("Hoi, mail naar support@example.org voor hulp." + sign, [])["paths"]


def test_payment_details_in_reply_fail() -> None:
    iban = "NL" + "91" + "ABNA" + "0417" + "1643" + "00"
    assert check(f"Hoi, wij zien {iban} en dat is het. Roosterly Support", [])["payment"]


def _luhn_number(prefix: str, length: int) -> str:
    """Build a Luhn-valid number at runtime so no card number sits in the source."""
    body = prefix + "0" * (length - len(prefix) - 1)
    for check_digit in "0123456789":
        if luhn_ok(body + check_digit):
            return body + check_digit
    raise AssertionError


def test_card_detection_uses_luhn() -> None:
    card = _luhn_number("4", 16)
    spaced = " ".join(card[i : i + 4] for i in range(0, 16, 4))
    assert card_numbers(f"kaart {spaced} graag") == [spaced]
    not_a_card = card[:-1] + str((int(card[-1]) + 1) % 10)
    assert card_numbers(f"order {not_a_card}") == []
    assert has_payment_details(spaced) and not has_payment_details("factuur 2026-10")
