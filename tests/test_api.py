from app import auth

OFFER = {"id": "245148", "titre": "Junior Forward Deployed Engineer", "entreprise": "YOOBIC", "score": 71}
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def test_nothing_is_readable_without_signing_in(anonymous, agent):
    agent.put("/api/offers/245148", json=OFFER)
    assert anonymous.get("/api/health").json() == {"ok": True}
    for path in ("/api/offers", "/api/offers/245148", "/api/meta/releve", "/api/files/abc"):
        assert anonymous.get(path).status_code == 401, path


def test_wrong_password_is_refused_then_slowed_down(anonymous):
    for _ in range(8):
        assert anonymous.post("/api/login", json={"password": "guess"}).status_code == 401
    assert anonymous.post("/api/login", json={"password": "guess"}).status_code == 429


def test_owner_reads_offers_after_signing_in(owner, agent):
    agent.put("/api/offers/245148", json=OFFER)
    offers = owner.get("/api/offers").json()
    assert offers == [OFFER | {"statut": "a_trier", "valide_le": None, "postule_le": None}]
    assert owner.get("/api/session").json() == {"signed_in": True, "configured": True}


def test_agent_rewrite_never_undoes_the_owners_decision(owner, agent):
    agent.put("/api/offers/245148", json=OFFER)
    validated = owner.patch("/api/offers/245148", json={"statut": "validee"}).json()
    assert validated["statut"] == "validee" and validated["valide_le"]

    # The morning agent sees the offer again, with a new score and a stale status.
    again = agent.put("/api/offers/245148", json=OFFER | {"score": 75, "statut": "a_trier"}).json()
    assert again["statut"] == "validee"
    assert again["valide_le"] == validated["valide_le"]
    assert again["score"] == 75
    assert again["titre"] == OFFER["titre"]


def test_owner_can_only_change_the_status(owner, agent):
    agent.put("/api/offers/245148", json=OFFER)
    assert owner.patch("/api/offers/245148", json={"score": 99}).status_code == 403
    assert owner.put("/api/offers/245148", json=OFFER).status_code == 401
    assert owner.patch("/api/offers/245148", json={"statut": "envoyee"}).status_code == 422
    applied = owner.patch("/api/offers/245148", json={"statut": "postulee"}).json()
    assert applied["statut"] == "postulee" and len(applied["postule_le"]) == 10


def test_agent_patch_merges_and_null_removes(agent):
    agent.put("/api/offers/245148", json=OFFER | {"pieces": "portfolio"})
    patched = agent.patch("/api/offers/245148", json={"lettre_id": "f1", "pieces": None}).json()
    assert patched["lettre_id"] == "f1" and "pieces" not in patched
    assert agent.patch("/api/offers/999", json={"score": 1}).status_code == 404


def test_import_keeps_the_original_decision_dates(agent):
    agent.put("/api/offers/245148", json=OFFER)
    imported = agent.patch(
        "/api/offers/245148",
        json={"statut": "validee", "valide_le": "2026-11-14T10:49:00+00:00", "postule_le": None},
    ).json()
    assert imported["valide_le"].startswith("2026-11-14T10:49")


def test_pdf_upload_and_download(owner, agent, anonymous):
    assert agent.post("/api/files?name=x.txt", content=b"hello").status_code == 415
    assert owner.post("/api/files?name=x.pdf", content=PDF).status_code == 401
    stored = agent.post("/api/files?name=Lettre YOOBIC.pdf", content=PDF).json()
    response = owner.get(stored["url"])
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="Lettre_YOOBIC.pdf"' in response.headers["content-disposition"]
    assert anonymous.get(stored["url"]).status_code == 401


def test_meta_documents(owner, agent):
    agent.put("/api/meta/releve", json={"analysees": 234, "retenues": 38})
    assert owner.get("/api/meta/releve").json() == {"analysees": 234, "retenues": 38}
    assert owner.put("/api/meta/releve", json={}).status_code == 401
    assert owner.get("/api/meta/nothing").status_code == 404


def test_forged_or_expired_sessions_are_refused(anonymous):
    token = auth.new_session()
    expires, nonce, signature = token.split(".")
    assert auth.session_is_valid(token)
    assert not auth.session_is_valid(f"{int(expires) + 1}.{nonce}.{signature}")
    assert not auth.session_is_valid("1.abc." + auth._signature("1.abc"))  # expired in 1970
    anonymous.cookies.set(auth.COOKIE, "forged")
    assert anonymous.get("/api/offers").status_code == 401


def test_logout_clears_the_cookie(owner):
    owner.post("/api/logout")
    assert owner.get("/api/offers").status_code == 401


def test_postings_keep_the_full_text_for_agents_and_owner(owner, agent, anonymous):
    stored = agent.put("/api/postings/245148", json={"text": "JUNIOR FORWARD DEPLOYED ENGINEER", "data": {"score": 71}})
    assert stored.json() == {"id": "245148", "chars": 32}
    assert owner.put("/api/postings/245148", json={"text": "x"}).status_code == 401
    assert owner.get("/api/postings/245148").json()["data"] == {"score": 71}
    assert anonymous.get("/api/postings/245148").status_code == 401
    assert agent.get("/api/postings/999").status_code == 404
