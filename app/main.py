import re
import secrets
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import auth
from .config import get_settings
from .db import get_db

app = FastAPI(title="Radar VIE", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
STATUSES = ("a_trier", "validee", "postulee", "passee")
# Fields the API exposes from the owner's columns, under the names the page uses.
OWNER_FIELDS = {"statut", "valide_le", "postule_le"}
MAX_FILE_BYTES = 4_000_000  # Vercel caps a request body at 4.5 MB
FAILED_LOGINS_PER_IP = 8
FAILED_LOGINS_TOTAL = 40

Db = Annotated[psycopg.Connection, Depends(get_db)]
Caller = Annotated[auth.Caller, Depends(auth.require_caller)]
AgentOnly = [Depends(auth.require_agent)]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def offer_out(row: dict) -> dict:
    """Flatten a row into the shape the page reads."""
    out = dict(row["data"])
    out["id"] = row["id"]
    out["statut"] = row["status"]
    out["valide_le"] = row["validated_at"].astimezone(UTC).isoformat() if row["validated_at"] else None
    out["postule_le"] = row["applied_at"].isoformat() if row["applied_at"] else None
    return out


def offer_or_404(conn: psycopg.Connection, offer_id: str) -> dict:
    row = conn.execute("SELECT * FROM offers WHERE id = %s", (offer_id,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such offer.")
    return row


# --- Session -------------------------------------------------------------------------


class Login(BaseModel):
    password: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/session")
def session(request: Request) -> dict:
    return {
        "signed_in": auth.session_is_valid(request.cookies.get(auth.COOKIE)),
        "configured": auth.sign_in_configured(),
    }


@app.post("/api/login")
def login(body: Login, request: Request, response: Response, conn: Db) -> dict:
    settings = get_settings()
    if not auth.sign_in_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is not configured yet.")
    ip = auth.client_ip(request)
    recent = conn.execute(
        """SELECT count(*) FILTER (WHERE ip = %s) AS mine, count(*) AS total
           FROM failed_logins WHERE at > now() - interval '15 minutes'""",
        (ip,),
    ).fetchone()
    if recent["mine"] >= FAILED_LOGINS_PER_IP or recent["total"] >= FAILED_LOGINS_TOTAL:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in 15 minutes.")
    if not auth.verify_password(body.password, settings.app_password_hash):
        conn.execute("INSERT INTO failed_logins (ip) VALUES (%s)", (ip,))
        conn.execute("DELETE FROM failed_logins WHERE at < now() - interval '1 day'")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password.")
    response.set_cookie(
        auth.COOKIE,
        auth.new_session(),
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


# --- Offers --------------------------------------------------------------------------


@app.get("/api/offers")
def list_offers(_: Caller, conn: Db) -> list[dict]:
    rows = conn.execute("SELECT * FROM offers ORDER BY id").fetchall()
    return [offer_out(r) for r in rows]


@app.get("/api/offers/{offer_id}")
def get_offer(offer_id: str, _: Caller, conn: Db) -> dict:
    return offer_out(offer_or_404(conn, offer_id))


@app.put("/api/offers/{offer_id}", dependencies=AgentOnly)
def put_offer(offer_id: str, conn: Db, body: Annotated[dict[str, Any], Body()]) -> dict:
    """Create an offer, or merge new fields into it. Never touches the owner's decision."""
    data = {k: v for k, v in body.items() if k not in OWNER_FIELDS | {"id"}}
    row = conn.execute(
        """INSERT INTO offers (id, data) VALUES (%s, %s)
           ON CONFLICT (id) DO UPDATE SET data = offers.data || EXCLUDED.data, updated_at = now()
           RETURNING *""",
        (offer_id, Jsonb(data)),
    ).fetchone()
    return offer_out(row)


@app.patch("/api/offers/{offer_id}")
def patch_offer(offer_id: str, caller: Caller, conn: Db, body: Annotated[dict[str, Any], Body()]) -> dict:
    """The owner may only change the status. Agents may also change data fields
    (a null value removes the field) and set the decision dates when importing."""
    offer_or_404(conn, offer_id)
    if caller == "owner" and set(body) - {"statut"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the status can be changed from the page.")

    sets: list[str] = []
    params: list[Any] = []
    new_status = body.get("statut")
    if new_status is not None:
        if new_status not in STATUSES:
            raise HTTPException(422, f"Status must be one of {', '.join(STATUSES)}.")
        sets.append("status = %s")
        params.append(new_status)
        if new_status == "validee" and "valide_le" not in body:
            sets.append("validated_at = now()")
        if new_status == "postulee" and "postule_le" not in body:
            sets.append("applied_at = (now() AT TIME ZONE %s)::date")
            params.append(get_settings().timezone)
    if "valide_le" in body:
        sets.append("validated_at = %s")
        params.append(datetime.fromisoformat(body["valide_le"]) if body["valide_le"] else None)
    if "postule_le" in body:
        sets.append("applied_at = %s")
        params.append(date.fromisoformat(body["postule_le"][:10]) if body["postule_le"] else None)

    fields = {k: v for k, v in body.items() if k not in OWNER_FIELDS | {"id"}}
    removed = [k for k, v in fields.items() if v is None]
    added = {k: v for k, v in fields.items() if v is not None}
    if fields:
        sets.append("data = (data - %s::text[]) || %s")
        params += [removed, Jsonb(added)]

    if not sets:
        raise HTTPException(422, "Nothing to change.")
    sets.append("updated_at = now()")
    row = conn.execute(
        f"UPDATE offers SET {', '.join(sets)} WHERE id = %s RETURNING *",  # column names are fixed above
        (*params, offer_id),
    ).fetchone()
    return offer_out(row)


@app.delete("/api/offers/{offer_id}", dependencies=AgentOnly)
def delete_offer(offer_id: str, conn: Db) -> dict:
    deleted = conn.execute("DELETE FROM offers WHERE id = %s", (offer_id,)).rowcount
    return {"deleted": bool(deleted)}


# --- Meta documents ------------------------------------------------------------------


@app.get("/api/meta/{key}")
def get_meta(key: str, _: Caller, conn: Db) -> dict:
    row = conn.execute("SELECT data FROM meta WHERE key = %s", (key,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing stored under this key.")
    return row["data"]


@app.put("/api/meta/{key}", dependencies=AgentOnly)
def put_meta(key: str, conn: Db, body: Annotated[dict[str, Any], Body()]) -> dict:
    conn.execute(
        """INSERT INTO meta (key, data) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()""",
        (key, Jsonb(body)),
    )
    return body


# --- Files ---------------------------------------------------------------------------


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "document"
    return cleaned if cleaned.lower().endswith(".pdf") else cleaned + ".pdf"


@app.post("/api/files", dependencies=AgentOnly)
async def upload_file(request: Request, conn: Db, name: Annotated[str, Query(max_length=200)]) -> dict:
    content = await request.body()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File over 4 MB.")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF files are accepted.")
    file_id = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO files (id, name, size, content) VALUES (%s, %s, %s, %s)",
        (file_id, safe_filename(name), len(content), content),
    )
    return {"id": file_id, "url": f"/api/files/{file_id}", "size": len(content)}


@app.get("/api/files/{file_id}")
def get_file(file_id: str, _: Caller, conn: Db) -> Response:
    row = conn.execute("SELECT name, type, content FROM files WHERE id = %s", (file_id,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file.")
    return Response(
        content=bytes(row["content"]),
        media_type=row["type"],
        headers={
            "Content-Disposition": f'inline; filename="{row["name"]}"',
            "Cache-Control": "private, max-age=86400",
        },
    )


@app.delete("/api/files/{file_id}", dependencies=AgentOnly)
def delete_file(file_id: str, conn: Db) -> dict:
    deleted = conn.execute("DELETE FROM files WHERE id = %s", (file_id,)).rowcount
    return {"deleted": bool(deleted)}


# Locally, uvicorn serves the page too. On Vercel, public/ is served by the CDN.
if PUBLIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="page")
