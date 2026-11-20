"""The morning run, free end to end: read the new offers, score them by code, write short texts with a
local model, store everything through the app's API, email the summary.

    uv run --extra collector python -m collector.run              # the real run (8:00, launchd)
    uv run --extra collector python -m collector.run --dry-run    # reads and scores, writes nothing
    uv run --extra collector python -m collector.run --ids 247753 248069 --dry-run   # specific offers

The profile (criteria, skills, cities) is a private TOML file: RADAR_PROFILE, by default
~/Desktop/ClaudeProject/VIE/veille/profil.toml. An example is in collector/profile.example.toml.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from . import llm, mailer
from .parse import fold, parse_offer
from .scoring import load_profile, score_offer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import radar  # noqa: E402  (the same small API client the other agents use)

DEFAULT_PROFILE = Path.home() / "Desktop/ClaudeProject/VIE/veille/profil.toml"
BASE = "https://mon-vie-via.businessfrance.fr"


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {message}", flush=True)


def app_offer(offer, facts: dict, score, texts: dict, today: date) -> dict:
    """The offer in the shape the app and the letters agent read."""
    return {
        "id": offer.id,
        "titre": offer.title.title() if offer.title.isupper() else offer.title,
        "entreprise": facts.get("entreprise") or offer.company,
        "ville": offer.city,
        "pays": offer.country,
        "priorite": score.priority,
        "score": score.total,
        "indemnite": offer.allowance,
        "duree_mois": offer.months,
        "debut": offer.start.isoformat() if offer.start else None,
        "publiee_le": offer.published.isoformat() if offer.published else None,
        "expire_le": offer.expires.isoformat() if offer.expires else None,
        "ajoutee_le": today.isoformat(),
        "competences": score.have + score.missing,
        "competences_manquantes": score.missing,
        "resume": texts.get("resume", ""),
        "pourquoi": texts.get("pourquoi", ""),
        "pieces": facts.get("pieces", ""),
        "cv_conseille": "sobre" if facts.get("portail_entreprise") else "Dune",
        "lien": f"{BASE}/offres/{offer.id}",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="read and score, but write nothing and send nothing")
    parser.add_argument("--ids", nargs="*", help="only these offer numbers (skips the list page)")
    parser.add_argument("--no-mail", action="store_true")
    args = parser.parse_args(argv)

    from .browser import Reader  # imported here so the scoring tests run without Playwright

    started = time.monotonic()
    today = date.today()
    profile = load_profile(os.environ.get("RADAR_PROFILE", DEFAULT_PROFILE))
    model = profile["model"]
    state_path = Path(profile["state_file"])
    state = json.loads(state_path.read_text()) if state_path.exists() else {"vues": []}
    seen = set(state["vues"])
    problems: list[str] = []

    existing = {o["id"] for o in radar.call("GET", "/api/offers")}
    skip_words = [fold(w) for w in profile.get("skip_titles", [])]

    # 1. Read: the list page, then each new offer that is a V.I.E. and not plainly off target.
    texts: dict[str, str | None] = {}
    with Reader() as reader:
        if args.ids:
            card_ids, read_count = list(args.ids), len(args.ids)
            to_open = list(args.ids)
        else:
            cards, read_count = reader.list_new_cards(profile["search_url"], seen)
            card_ids = [c.id for c in cards]
            to_open = [
                c.id for c in cards
                if c.kind != "VIA" and c.id not in existing and not any(w in f" {fold(c.title)} " for w in skip_words)
            ]
            log(f"{read_count} cartes lues, {len(cards)} nouvelles, {len(to_open)} à ouvrir")
        for offer_id in to_open:
            texts[offer_id] = reader.offer_text(offer_id)
            if texts[offer_id] is None:
                log(f"{offer_id} : offre retirée du site")

    # 2. Facts from the local model, score from code. Every offer read is kept whole in the app.
    kept = []
    for offer_id, text in texts.items():
        if not text:
            continue
        offer = parse_offer(offer_id, text)
        try:
            facts = llm.read_facts(model, profile["roles"], text)
        except Exception as err:  # one bad answer must not stop the morning
            problems.append(f"L'offre {offer_id} n'a pas pu être lue par le modèle local ({err}).")
            continue
        score = score_offer(profile, offer, facts, today)
        log(f"{offer_id} {offer.title[:50]} · {offer.city} · {score.total} ({score.role}+{score.skills}+{score.conditions})")
        if not args.dry_run:
            radar.call("PUT", f"/api/postings/{offer_id}", {"text": text, "data": {"facts": facts, "score": score.__dict__}})
        if score.total >= profile.get("min_score", 55) and score.priority is not None:
            kept.append((offer, facts, score, text))

    # 3. Short texts, only for the offers kept.
    new_offers = []
    for offer, facts, score, text in kept:
        computed = {
            "poste": facts.get("categorie"), "competences_qu_il_a": score.have,
            "competences_qui_manquent": score.missing, "remarques": score.notes,
        }
        try:
            written = llm.write_texts(model, text, computed)
        except Exception as err:
            problems.append(f"Pas de résumé pour l'offre {offer.id} ({err}).")
            written = {}
        new_offers.append(app_offer(offer, facts, score, written, today))
    llm.unload(model)

    # 4. Store, remember what was read, email.
    run_info = {"date": datetime.now().astimezone().isoformat(timespec="seconds"), "analysees": read_count,
                "retenues": len(new_offers), "par": "collector"}
    if args.dry_run:
        print(json.dumps(new_offers, ensure_ascii=False, indent=2))
    else:
        if new_offers:
            for o in new_offers:
                radar.call("PUT", f"/api/offers/{o['id']}", o)
        radar.call("PUT", "/api/meta/releve", run_info)
        state["vues"] = sorted(seen | set(card_ids))
        state["derniere_execution"] = run_info["date"]
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))

    if not args.dry_run and not args.no_mail:
        bands = {p["level"]: p["name"] for p in profile["priorities"]}
        subject, text_body, html_body = mailer.build(new_offers, bands, profile["app_url"], today, problems)
        sent = mailer.send(profile["mail_to"], profile["mail_to"], subject, text_body, html_body)
        log("mail envoyé" if sent else "mail non envoyé : mot de passe d'application Gmail absent du trousseau")

    log(f"terminé en {round(time.monotonic() - started)} s : {read_count} lues, {len(new_offers)} retenues"
        + (f", {len(problems)} problème(s)" if problems else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
