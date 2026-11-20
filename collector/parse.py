"""Turns the rendered text of an offer page into fields. Plain code: the page always has the same labels."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}

# Country names as Business France writes them, with the French spelling the app shows.
COUNTRIES = {
    "ETATS-UNIS": "États-Unis", "CANADA": "Canada", "ROYAUME-UNI": "Royaume-Uni", "IRLANDE": "Irlande",
    "ITALIE": "Italie", "NORVEGE": "Norvège", "SUEDE": "Suède", "AUSTRALIE": "Australie",
    "NOUVELLE-ZELANDE": "Nouvelle-Zélande",
}


def fold(text: str) -> str:
    """Upper case without accents, for comparisons: "Norvège" -> "NORVEGE"."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper().strip()


def _french_date(text: str) -> date | None:
    m = re.search(r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})", text, re.IGNORECASE)
    if not m:
        return None
    month = MONTHS.get(fold(m.group(2)).lower())
    return date(int(m.group(3)), month, int(m.group(1))) if month else None


def clean_city(raw: str) -> str:
    """"NEW YORK -NY-" -> "New York", "SAN FRANCISCO -CA-" -> "San Francisco"."""
    city = re.sub(r"\s*-[A-Z]{2}-\s*$", "", raw.strip())
    small = {"de", "du", "la", "le", "les", "sur", "en"}
    words = re.split(r"(\s+|-)", city.lower())
    return "".join(w if w in small and i else w.capitalize() for i, w in enumerate(words)).strip()


@dataclass
class Offer:
    id: str
    company: str = ""
    title: str = ""
    country: str = ""  # French spelling, "États-Unis"
    city: str = ""
    kind: str = ""  # VIE or VIA
    start: date | None = None
    end: date | None = None
    months: int | None = None
    allowance: int | None = None  # euros per month
    published: date | None = None
    expires: date | None = None
    company_text: str = ""
    mission: str = ""
    profile: str = ""
    raw: str = field(default="", repr=False)


def parse_offer(offer_id: str, text: str) -> Offer:
    lines = [line.strip() for line in text.splitlines()]
    nonempty = [line for line in lines if line]
    offer = Offer(id=offer_id, raw=text)
    if len(nonempty) >= 3:
        offer.company, offer.title = nonempty[0], re.sub(r"\s*\((H/F|F/H|M/F)\)\s*$", "", nonempty[1], flags=re.I)
        offer.kind = "VIA" if re.search(r"\bVIA\b", nonempty[2]) else "VIE"

    # "ETATS-UNIS (NEW YORK -NY-)" in the "LA MISSION" block; titles can look the same, so only
    # a known country counts.
    mission_block = text[text.find("LA MISSION"):] if "LA MISSION" in text else text
    for place in re.finditer(r"^([A-ZÉÈÊÀÔÛÎÇ' -]+?)\s*\(([^)]+)\)\s*$", mission_block, re.MULTILINE):
        if fold(place.group(1)) in COUNTRIES:
            offer.country = COUNTRIES[fold(place.group(1))]
            offer.city = clean_city(place.group(2))
            break

    period = re.search(r"du (.+?) au (.+?) \((\d+) mois\)", text)
    if period:
        offer.start, offer.end, offer.months = _french_date(period.group(1)), _french_date(period.group(2)), int(period.group(3))

    pay = re.search(r"REMUNERATION MENSUELLE\s*:\s*([\d\s.,]+)\s*€", text)
    if pay:
        offer.allowance = round(float(pay.group(1).replace(" ", "").replace(",", ".")))

    published = re.search(r"Date de publication\s*:\s*(.+)", text)
    expires = re.search(r"Date d'expiration\s*:\s*(.+)", text)
    offer.published = _french_date(published.group(1)) if published else None
    offer.expires = _french_date(expires.group(1)) if expires else None

    def section(start: str, *ends: str) -> str:
        m = re.search(re.escape(start) + r"(.*?)(?=" + "|".join(re.escape(e) for e in ends) + r"|\Z)", text, re.S)
        return re.sub(r"\n{3,}", "\n\n", m.group(1)).strip() if m else ""

    offer.mission = section("POSTE ET MISSION", "LE PROFIL IDÉAL", "INFORMATIONS COMPLÉMENTAIRES")
    offer.profile = section("LE PROFIL IDÉAL", "INFORMATIONS COMPLÉMENTAIRES")
    offer.company_text = section("Favoris", "QUELQUES CHIFFRES", "LA MISSION")
    return offer
