"""The only steps that need a language model, run locally with Ollama (free, nothing leaves the Mac).

1. `read_facts`: the model reads an offer and answers a fixed JSON form (category, requested skills,
   languages...). It never scores: the code does (scoring.py).
2. `write_texts`: for the offers kept, a two-sentence summary and one "why" sentence, written only
   from facts the code computed.
"""

import json
import urllib.error
import urllib.request

OLLAMA = "http://localhost:11434"

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "entreprise": {"type": "string"},
        "categorie": {"type": "string", "enum": ["coeur", "data", "acceptable", "hors_cible"]},
        "competences": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8},
        "langues_obligatoires": {"type": "array", "items": {"type": "string"}},
        "langues_atout": {"type": "array", "items": {"type": "string"}},
        "niveau_bac5_ok": {"type": "boolean"},
        "secteur_mode_luxe_sport": {"type": "boolean"},
        "portail_entreprise": {"type": "boolean"},
        "pieces": {"type": "string"},
    },
    "required": ["entreprise", "categorie", "competences", "langues_obligatoires", "langues_atout", "niveau_bac5_ok",
                 "secteur_mode_luxe_sport", "portail_entreprise", "pieces"],
}

TEXTS_SCHEMA = {
    "type": "object",
    "properties": {"resume": {"type": "string"}, "pourquoi": {"type": "string"}},
    "required": ["resume", "pourquoi"],
}


def _post(body: dict) -> dict:
    request = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        raise RuntimeError(json.loads(err.read() or b"{}").get("error", str(err))) from None


def _first_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def _check(answer: dict, schema: dict) -> dict:
    missing = [k for k in schema["required"] if k not in answer]
    if missing:
        raise ValueError(f"answer lacks {missing}")
    return answer


def _chat(model: str, system: str, user: str, schema: dict) -> dict:
    """Ask for an answer in the schema's exact shape. Ollama enforces it while generating when the model
    supports it ("structured outputs"); otherwise the schema goes in the prompt and the answer is checked."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    try:
        return _check(json.loads(_post(body)["message"]["content"]), schema)
    except RuntimeError as err:
        if "structured output" not in str(err):
            raise
    del body["format"]
    body["messages"][0]["content"] += "\n\nRéponds uniquement par un objet JSON conforme à ce schéma :\n" + json.dumps(schema, ensure_ascii=False)
    for _ in range(2):
        try:
            return _check(_first_json_object(_post(body)["message"]["content"]), schema)
        except ValueError:
            continue
    raise ValueError("the model did not return the expected JSON twice in a row")


def unload(model: str) -> None:
    """Free the memory right away (the Mac has 8 GB)."""
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/generate", data=body, headers={"Content-Type": "application/json"}), timeout=30)
    except Exception:
        pass


def read_facts(model: str, roles: dict, offer_text: str) -> dict:
    categories = "\n".join(f"- {name} : {r['definition']}" for name, r in roles.items())
    system = (
        "Tu lis une offre de V.I.E. et tu remplis un formulaire JSON. Tu ne notes rien et tu n'inventes rien : "
        "tu rapportes ce que dit l'offre.\n"
        f"categorie, d'après l'intitulé et la mission :\n{categories}\n"
        "competences : 4 à 8 compétences ou outils que l'offre demande vraiment, en mots courts (\"Python\", \"SQL\", "
        "\"Kubernetes\", \"relation client\", \"audit interne\"). Pas de qualités personnelles (curiosité, autonomie, rigueur). "
        "Pas de langues.\n"
        "langues_obligatoires : seulement les langues dites indispensables, exigées, requises, obligatoires "
        "(\"required\", \"must\", \"fluent ... required\"). langues_atout : celles présentées comme un atout, un plus, "
        "appréciées, souhaitées (\"nice to have\", \"is a plus\", \"preferred\"). Une langue ne va que dans une des deux listes.\n"
        "niveau_bac5_ok : faux seulement si l'offre exige un doctorat ou refuse un bac+5.\n"
        "secteur_mode_luxe_sport : vrai si l'entreprise est dans la mode, le luxe, la beauté ou le sport.\n"
        "portail_entreprise : vrai si la candidature passe par le site carrières de l'entreprise (Workday, Taleo, "
        "Greenhouse, lien vers un site de recrutement), faux si elle passe par Business France.\n"
        "entreprise : le nom court et connu de l'entreprise, celui de sa marque (\"Coface\" et non \"Compagnie Française d'Assurance pour le Commerce Extérieur\", \"L'Occitane\" et non \"M&L Distribution\"), pas la raison sociale.\n"
        "pieces : ce que l'offre demande en plus du CV et de la lettre (portfolio, références, questions), ou \"\"."
    )
    return _chat(model, system, offer_text[:12000], FACTS_SCHEMA)


def write_texts(model: str, offer_text: str, facts: dict) -> dict:
    system = (
        "Tu écris en français, phrases courtes et simples, sans tiret cadratin ni emoji.\n"
        "resume : exactement deux phrases sur la mission, d'après l'offre.\n"
        "pourquoi : une seule phrase sur le candidat, à la troisième personne (\"il\"), qui dit ce qui colle et ce qui manque, "
        "en utilisant uniquement les faits fournis (compétences qu'il a, compétences qui manquent, remarques). "
        "Exemple : \"Poste data qui colle : il a Python, SQL et Power BI ; il lui manque Tableau.\" "
        "Ne lui prête aucune autre compétence."
    )
    user = (
        "FAITS CALCULÉS\n" + json.dumps(facts, ensure_ascii=False) + "\n\nOFFRE\n" + offer_text[:10000]
    )
    return _chat(model, system, user, TEXTS_SCHEMA)
