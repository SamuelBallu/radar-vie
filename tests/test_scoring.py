"""The scoring rules, checked without a browser, a model or a database."""

from datetime import date
from pathlib import Path

from collector.parse import parse_offer
from collector.scoring import SkillMatcher, load_profile, only_a_plus, score_offer, title_category

PROFILE = load_profile(Path(__file__).resolve().parent.parent / "collector" / "profile.example.toml")
TEXT = """ACME
SOLUTIONS ENGINEER (H/F)
NEW YORK -NY-  VIE  mars 2027  12 mois
 LA MISSION
ETATS-UNIS (NEW YORK -NY-)
du 01 mars 2027 au 01 mars 2028 (12 mois)
REMUNERATION MENSUELLE : 5088.87 € (indemnité)
POSTE ET MISSION
Help clients deploy our platform.
LE PROFIL IDÉAL
Fluent English required. Italian is a plus.
 INFORMATIONS COMPLÉMENTAIRES
REFERENCE : VIE1
Date de publication : 02 novembre 2026
Date d'expiration : 02 décembre 2026
"""
FACTS = {"categorie": "acceptable", "competences": ["Python", "SQL"], "langues_obligatoires": ["anglais"],
         "langues_atout": ["italien"], "niveau_bac5_ok": True, "secteur_mode_luxe_sport": False}


def test_a_skill_is_never_inferred_from_a_neighbour():
    m = SkillMatcher.from_profile(PROFILE)
    assert m.owned("React") and not m.owned("JavaScript")
    assert m.owned("OpenShift") and not m.owned("Kubernetes")
    assert not m.owned("AWS") and not m.owned("NoSQL")


def test_declared_synonyms_and_longer_names_count():
    m = SkillMatcher.from_profile(PROFILE)
    assert m.owned("IA générative") == "LLM"
    assert m.owned("Python programming") == "Python"
    assert m.owned("Azure OpenAI Service") == "Azure OpenAI"
    assert not m.owned("Azure")


def test_offer_page_is_parsed():
    o = parse_offer("1", TEXT)
    assert (o.country, o.city, o.allowance, o.months) == ("États-Unis", "New York", 5089, 12)
    assert (o.start, o.expires) == (date(2027, 3, 1), date(2026, 12, 2))


def test_title_settles_the_category_before_the_model():
    assert title_category(PROFILE, "SOLUTIONS ENGINEER") == "coeur"
    assert title_category(PROFILE, "Data Scientist") == "data"
    assert title_category(PROFILE, "Junior Buyer") is None


def test_score_adds_up_and_bonus_languages_do_not_block():
    s = score_offer(PROFILE, parse_offer("1", TEXT), FACTS, date(2026, 11, 20))
    assert (s.role, s.skills, s.conditions, s.priority) == (50, 30, 20, 1)
    assert s.total == 100


def test_a_required_language_he_does_not_speak_removes_the_offer():
    facts = FACTS | {"langues_obligatoires": ["anglais", "allemand"]}
    text = TEXT.replace("Italian is a plus.", "German is required.")
    assert score_offer(PROFILE, parse_offer("1", text), facts, date(2026, 11, 20)).total == 0


def test_a_language_only_named_as_a_plus_is_not_required():
    assert only_a_plus("La maîtrise de l'italien constitue un atout.", "italien")
    assert not only_a_plus("Italian is required.", "italien")


def test_languages_listed_as_skills_are_ignored():
    facts = FACTS | {"competences": ["Python", "Spanish", "espagnol"]}
    s = score_offer(PROFILE, parse_offer("1", TEXT), facts, date(2026, 11, 20))
    assert s.have == ["Python"] and s.missing == []
