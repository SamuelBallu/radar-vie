"""The score, computed by code from the owner's profile. Same rules every day, and every point explained.

Score out of 100 = role (0-50) + skills (0-30) + conditions (0-20).
A skill counts only if it is in the owner's list, or is a declared synonym of one. Similar-sounding
skills never count (OpenShift is not Kubernetes): that rule lives here, not in a prompt.
"""

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .parse import Offer, fold


def load_profile(path: str | Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _key(skill: str) -> str:
    return re.sub(r"[^A-Z0-9+#/ ]", " ", fold(skill)).strip()


@dataclass
class SkillMatcher:
    acquired: dict[str, str]  # folded key -> name as the owner wrote it
    basics: dict[str, str]
    synonyms: dict[str, str]  # folded alias -> owner's name

    @classmethod
    def from_profile(cls, profile: dict) -> "SkillMatcher":
        skills = profile["skills"]
        return cls(
            acquired={_key(s): s for s in skills["acquired"]},
            basics={_key(s): s for s in skills.get("basics", [])},
            synonyms={_key(a): s for a, s in skills.get("synonyms", {}).items()},
        )

    def owned(self, skill: str) -> str | None:
        """The owner's skill this requested skill is, or None. Exact name or declared synonym only,
        or the owner's skill written as a whole word inside it ("Python programming" -> Python)."""
        k = _key(skill)
        for table in (self.acquired, self.basics, self.synonyms):
            if k in table:
                return table[k]
        for table in (self.acquired, self.basics, self.synonyms):
            for known, name in table.items():
                if len(known) > 2 and re.search(rf"(?<![A-Z0-9]){re.escape(known)}(?![A-Z0-9])", k):
                    return name
        return None


@dataclass
class Score:
    total: int
    role: int
    skills: int
    conditions: int
    priority: int | None
    have: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def priority(profile: dict, offer: Offer) -> int | None:
    city = fold(offer.city)
    for level in profile["priorities"]:
        if any(c == city or city.startswith(c + " ") for c in level.get("cities", [])):
            return level["level"]
        if offer.country in level.get("countries", []):
            return level["level"]
    return None


# The words a language appears with when it is a bonus, not a requirement.
PLUS_WORDS = re.compile(r"ATOUT|UN PLUS|\bA PLUS\b|APPRECI|SOUHAIT|NICE TO HAVE|PREFERRED|ADVANTAGE|ASSET|BONUS|DESIRABLE", re.I)
LANGUAGE_STEMS = {"ANGLAIS": "ENGLISH", "FRANCAIS": "FRENCH", "ITALIEN": "ITALIAN", "ALLEMAND": "GERMAN",
                  "ESPAGNOL": "SPANISH", "NORVEGIEN": "NORWEGIAN", "SUEDOIS": "SWEDISH", "COREEN": "KOREAN",
                  "JAPONAIS": "JAPANESE", "CHINOIS": "CHINESE", "PORTUGAIS": "PORTUGUESE", "NEERLANDAIS": "DUTCH"}


def only_a_plus(text: str, language: str) -> bool:
    """True when every line naming this language presents it as a bonus. A safety net under the model,
    which sometimes lists bonus languages as required."""
    name = fold(language)
    names = {name} | {v for k, v in LANGUAGE_STEMS.items() if k.startswith(name[:5])} | {k for k, v in LANGUAGE_STEMS.items() if v.startswith(name[:5])}
    lines = [fold(line) for line in text.splitlines() if any(n[:5] in fold(line) for n in names)]
    return bool(lines) and all(PLUS_WORDS.search(line) for line in lines)


def is_language(skill: str) -> bool:
    name = fold(skill)
    return any(name.startswith(k[:5]) or name.startswith(v[:5]) for k, v in LANGUAGE_STEMS.items())


def title_category(profile: dict, title: str) -> str | None:
    """The role category when the job title settles it (checked in the profile's order)."""
    t = f" {fold(title)} "
    for category, words in profile.get("role_titles", {}).items():
        if any(fold(w) in t for w in words):
            return category
    return None


def months_between(a: date, b: date) -> float:
    return (b - a).days / 30.44


def score_offer(profile: dict, offer: Offer, facts: dict, today: date) -> Score:
    """`facts` comes from the language model: category, requested skills, languages, level, sector."""
    notes: list[str] = []
    category = title_category(profile, offer.title) or facts.get("categorie", "hors_cible")
    role = profile["roles"].get(category, {"points": 0})["points"]

    matcher = SkillMatcher.from_profile(profile)
    # A language is a condition, not a skill (the model sometimes lists "Spanish" among the skills).
    requested = [s.strip() for s in facts.get("competences", []) if s and s.strip() and not is_language(s)]
    have, missing = [], []
    for s in dict.fromkeys(requested):  # keep order, drop duplicates
        (have if matcher.owned(s) else missing).append(s)
    skills = round(30 * len(have) / len(requested)) if requested else 15

    ok_languages = {fold(x) for x in profile["conditions"]["languages_ok"]}
    required = [x for x in facts.get("langues_obligatoires", []) if not only_a_plus(offer.raw, x)]
    other_languages = [x for x in required if not any(ok in fold(x) for ok in ok_languages)]
    language = 0 if other_languages else 8
    if other_languages:
        notes.append("demande " + ", ".join(other_languages))
    if offer.start is None:
        start = 4
    else:
        gap = months_between(today, offer.start)
        start = 6 if gap >= profile.get("notice_months", 3) - 0.25 else 3 if gap >= 2 else 0
        if start < 6:
            notes.append(f"début le {offer.start:%d/%m/%Y}, juste avec 3 mois de préavis")
    level = 6 if facts.get("niveau_bac5_ok", True) else 0
    bonus = profile["conditions"].get("sector_bonus", 5) if facts.get("secteur_mode_luxe_sport") else 0
    conditions = min(20, language + start + level + bonus)

    # An offer that requires a language he does not speak, or is off target, is not shown at all.
    total = 0 if (role == 0 or other_languages) else role + skills + conditions
    return Score(total, role, skills, conditions, priority(profile, offer), have, missing, notes)
