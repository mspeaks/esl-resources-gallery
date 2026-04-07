"""
ESL Gallery API — FastAPI + SQLite
Port 8915
"""

import os, sqlite3, secrets, datetime, json, shutil, subprocess, base64, glob
from typing import Optional, List
from contextlib import contextmanager
from google import genai

from fastapi import FastAPI, HTTPException, Depends, Cookie, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import bcrypt

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ESL_DB_PATH", os.path.join(BASE_DIR, "gallery.db"))
PDF_DIR = os.environ.get("ESL_PDF_DIR", os.path.join(BASE_DIR, "media", "pdf"))
IMG_DIR = os.environ.get("ESL_IMG_DIR", os.path.join(BASE_DIR, "media", "img"))
SESSION_DAYS = int(os.environ.get("ESL_SESSION_DAYS", "30"))
CORS_ORIGINS = os.environ.get("ESL_CORS_ORIGINS", "http://localhost:8080").split(",")
DEFAULT_ADMIN_USER = os.environ.get("ESL_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASS = os.environ.get("ESL_ADMIN_PASS", "")

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="ESL Gallery API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DB helpers ────────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'teacher',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                level TEXT NOT NULL,
                grammar TEXT NOT NULL,
                type TEXT NOT NULL,
                thumbnail TEXT NOT NULL DEFAULT '',
                pages TEXT NOT NULL DEFAULT '[]',
                pdf_path TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                notes_updated_at TEXT,
                notes_updated_by INTEGER,
                uploaded_by INTEGER,
                status TEXT NOT NULL DEFAULT 'approved',
                rejection_comment TEXT,
                submitted_at TEXT NOT NULL,
                approved_at TEXT,
                FOREIGN KEY (uploaded_by) REFERENCES users(id),
                FOREIGN KEY (notes_updated_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS log_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                entry TEXT NOT NULL,
                rating INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (resource_id) REFERENCES resources(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)

        # Seed coordinator account if not exists (set ESL_ADMIN_PASS to enable)
        if DEFAULT_ADMIN_PASS:
            existing = conn.execute("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USER,)).fetchone()
            if not existing:
                pw_hash = bcrypt.hashpw(DEFAULT_ADMIN_PASS.encode(), bcrypt.gensalt()).decode()
                now = datetime.datetime.utcnow().isoformat()
                conn.execute(
                    "INSERT INTO users (username, display_name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                    (DEFAULT_ADMIN_USER, DEFAULT_ADMIN_USER.title(), pw_hash, "coordinator", now)
                )


init_db()


# ── Auth helpers ──────────────────────────────────────────────────────────────
def now_iso():
    return datetime.datetime.utcnow().isoformat()


def get_current_user(session_token: Optional[str] = Cookie(None, alias="esl_session")):
    if not session_token:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT s.user_id, s.expires_at, u.username, u.display_name, u.role "
            "FROM sessions s JOIN users u ON s.user_id = u.id "
            "WHERE s.token = ?",
            (session_token,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < now_iso():
            conn.execute("DELETE FROM sessions WHERE token = ?", (session_token,))
            return None
        return dict(row)


def require_auth(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_coordinator(user=Depends(require_auth)):
    if user["role"] != "coordinator":
        raise HTTPException(status_code=403, detail="Coordinator access required")
    return user


# ── Pydantic models ───────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    username: str
    password: str


class UserCreateIn(BaseModel):
    username: str
    display_name: str
    password: str
    role: str = "teacher"


class NotesUpdateIn(BaseModel):
    notes: str


class LogEntryIn(BaseModel):
    entry: str
    rating: Optional[int] = None


class RejectIn(BaseModel):
    comment: Optional[str] = None


class ResourceSubmitIn(BaseModel):
    title: str
    description: str
    level: str
    grammar: str  # comma-separated tags
    type: str


# ── AUTH ENDPOINTS ────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
def login(body: LoginIn):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (body.username,)
        ).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        token = secrets.token_urlsafe(32)
        expires = (datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_DAYS)).isoformat()
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
            (token, user["id"], expires)
        )

    response = JSONResponse({"ok": True, "username": user["username"], "role": user["role"]})
    response.set_cookie(
        "esl_session", token,
        httponly=True, samesite="lax", secure=True,
        max_age=SESSION_DAYS * 86400, path="/"
    )
    return response


@app.post("/api/auth/logout")
def logout(session_token: Optional[str] = Cookie(None, alias="esl_session")):
    if session_token:
        with get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (session_token,))
    response = JSONResponse({"ok": True})
    response.delete_cookie("esl_session", path="/")
    return response


@app.get("/api/auth/me")
def me(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "id": user["user_id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"]
    }


# ── USER MANAGEMENT ───────────────────────────────────────────────────────────
@app.get("/api/users")
def list_users(coordinator=Depends(require_coordinator)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, role, created_at FROM users ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/users")
def create_user(body: UserCreateIn, coordinator=Depends(require_coordinator)):
    if body.role not in ("teacher", "coordinator"):
        raise HTTPException(status_code=400, detail="Invalid role")
    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (body.username,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            (body.username, body.display_name, pw_hash, body.role, now_iso())
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"ok": True, "id": user_id}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, coordinator=Depends(require_coordinator)):
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}


# ── RESOURCES ─────────────────────────────────────────────────────────────────
def compute_avg_rating(conn, resource_id: int) -> Optional[float]:
    """
    Average of most recent rating per teacher per resource.
    """
    rows = conn.execute("""
        SELECT user_id, rating
        FROM log_entries
        WHERE resource_id = ? AND rating IS NOT NULL
        ORDER BY created_at DESC
    """, (resource_id,)).fetchall()
    seen = {}
    for row in rows:
        if row["user_id"] not in seen:
            seen[row["user_id"]] = row["rating"]
    if not seen:
        return None
    return round(sum(seen.values()) / len(seen), 2)


def resource_to_dict(row, avg_rating=None, uploader_name=None):
    d = dict(row)
    # Parse JSON fields
    try:
        d["pages"] = json.loads(d.get("pages", "[]") or "[]")
    except Exception:
        d["pages"] = []
    try:
        d["grammar"] = json.loads(d.get("grammar", "[]") or "[]")
    except Exception:
        d["grammar"] = d.get("grammar", "")
    d["avg_rating"] = avg_rating
    d["uploader_name"] = uploader_name
    return d


@app.get("/api/resources")
def list_resources(
    level: Optional[str] = None,
    grammar: Optional[str] = None,
    type: Optional[str] = None,
    q: Optional[str] = None,
    user=Depends(get_current_user)
):
    query = "SELECT r.*, u.display_name as uploader_name FROM resources r LEFT JOIN users u ON r.uploaded_by = u.id WHERE r.status = 'approved'"
    params = []
    if level:
        query += " AND r.level = ?"
        params.append(level)
    if type:
        query += " AND r.type = ?"
        params.append(type)
    if q:
        query += " AND (r.title LIKE ? OR r.description LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            avg = compute_avg_rating(conn, row["id"])
            d = resource_to_dict(row, avg_rating=avg, uploader_name=row["uploader_name"])
            # Grammar filter (server-side, since grammar is JSON array)
            if grammar:
                tags = [t.strip() for t in grammar.split(",")]
                resource_tags = d["grammar"] if isinstance(d["grammar"], list) else []
                if not any(t in resource_tags for t in tags):
                    continue
            results.append(d)

    return results


@app.post("/api/resources")
def submit_resource(body: ResourceSubmitIn, user=Depends(require_auth)):
    grammar_list = [t.strip() for t in body.grammar.split(",") if t.strip()]
    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO resources (title, description, level, grammar, type,
               uploaded_by, status, submitted_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (body.title, body.description, body.level,
             json.dumps(grammar_list), body.type,
             user["user_id"], "pending", now)
        )
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"ok": True, "id": rid, "status": "pending"}


@app.get("/api/resources/{resource_id}")
def get_resource(resource_id: int, user=Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT r.*, u.display_name as uploader_name FROM resources r "
            "LEFT JOIN users u ON r.uploaded_by = u.id "
            "WHERE r.id = ?", (resource_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Resource not found")

        # Non-auth can only see approved
        if row["status"] != "approved":
            if not user:
                raise HTTPException(status_code=403, detail="Forbidden")
            if user["role"] != "coordinator" and user["user_id"] != row["uploaded_by"]:
                raise HTTPException(status_code=403, detail="Forbidden")

        avg = compute_avg_rating(conn, resource_id)

        # Log entries (only for authenticated users)
        logs = []
        if user:
            log_rows = conn.execute(
                "SELECT le.*, u.display_name FROM log_entries le "
                "JOIN users u ON le.user_id = u.id "
                "WHERE le.resource_id = ? ORDER BY le.created_at DESC",
                (resource_id,)
            ).fetchall()
            logs = [dict(lr) for lr in log_rows]

        d = resource_to_dict(row, avg_rating=avg, uploader_name=row["uploader_name"])
        d["log"] = logs
    return d


@app.put("/api/resources/{resource_id}/notes")
def update_notes(resource_id: int, body: NotesUpdateIn, user=Depends(require_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Resource not found")
        if user["role"] != "coordinator" and user["user_id"] != row["uploaded_by"]:
            raise HTTPException(status_code=403, detail="Only the uploader or coordinator can edit notes")
        now = now_iso()
        conn.execute(
            "UPDATE resources SET notes=?, notes_updated_at=?, notes_updated_by=? WHERE id=?",
            (body.notes, now, user["user_id"], resource_id)
        )
    return {"ok": True, "updated_at": now}


@app.post("/api/resources/{resource_id}/approve")
def approve_resource(resource_id: int, coordinator=Depends(require_coordinator)):
    now = now_iso()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        conn.execute(
            "UPDATE resources SET status='approved', approved_at=?, rejection_comment=NULL WHERE id=?",
            (now, resource_id)
        )
    return {"ok": True}


@app.post("/api/resources/{resource_id}/reject")
def reject_resource(resource_id: int, body: RejectIn, coordinator=Depends(require_coordinator)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        conn.execute(
            "UPDATE resources SET status='rejected', rejection_comment=? WHERE id=?",
            (body.comment, resource_id)
        )
    return {"ok": True}


@app.delete("/api/resources/{resource_id}")
def delete_resource(resource_id: int, coordinator=Depends(require_coordinator)):
    with get_db() as conn:
        conn.execute("DELETE FROM log_entries WHERE resource_id = ?", (resource_id,))
        conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
    return {"ok": True}


# ── LOG ENTRIES ───────────────────────────────────────────────────────────────
@app.get("/api/resources/{resource_id}/log")
def get_log(resource_id: int, user=Depends(require_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT le.*, u.display_name FROM log_entries le "
            "JOIN users u ON le.user_id = u.id "
            "WHERE le.resource_id = ? ORDER BY le.created_at DESC",
            (resource_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/resources/{resource_id}/log")
def add_log_entry(resource_id: int, body: LogEntryIn, user=Depends(require_auth)):
    if body.rating is not None and not (1 <= body.rating <= 5):
        raise HTTPException(status_code=400, detail="Rating must be 1-5")
    with get_db() as conn:
        row = conn.execute("SELECT id FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Resource not found")
        conn.execute(
            "INSERT INTO log_entries (resource_id, user_id, entry, rating, created_at) VALUES (?,?,?,?,?)",
            (resource_id, user["user_id"], body.entry, body.rating, now_iso())
        )
        entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"ok": True, "id": entry_id}


@app.delete("/api/resources/{resource_id}/log/{log_id}")
def delete_log_entry(resource_id: int, log_id: int, coordinator=Depends(require_coordinator)):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM log_entries WHERE id = ? AND resource_id = ?",
            (log_id, resource_id)
        )
    return {"ok": True}


# ── PENDING QUEUE ─────────────────────────────────────────────────────────────
@app.get("/api/pending")
def get_pending(coordinator=Depends(require_coordinator)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT r.*, u.display_name as uploader_name FROM resources r "
            "LEFT JOIN users u ON r.uploaded_by = u.id "
            "WHERE r.status = 'pending' ORDER BY r.submitted_at DESC"
        ).fetchall()
        results = []
        for row in rows:
            d = resource_to_dict(row)
            results.append(d)
    return results


# ── AI ANALYSIS HELPER ───────────────────────────────────────────────────────
def analyse_pages_with_ai(page_files: list, level: str) -> dict:
    """Send up to 3 page images to Gemini Flash for metadata extraction."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key or not page_files:
        return {}

    try:
        client = genai.Client(api_key=api_key)

        # Use first 3 pages (enough to identify the resource)
        sample_pages = page_files[:3]
        parts = []
        for p in sample_pages:
            with open(p, "rb") as f:
                img_data = f.read()
            parts.append(genai.types.Part.from_bytes(data=img_data, mime_type="image/jpeg"))

        prompt = f"""This is an ESL teaching resource at {level} level. Analyse the page(s) and return ONLY valid JSON with these fields:
{{
  "title": "Clear, descriptive title for a teacher gallery",
  "description": "1-2 sentences: what the activity is and how students use it",
  "grammar": ["Tag1", "Tag2"],
  "type": "one of: worksheet | game | speaking | writing | gap-fill | matching | error-correction | reference",
  "teacher_notes_pages": [list of 1-based page numbers that are teacher notes, not student-facing]
}}

Grammar tags must come from: Present Simple, Present Continuous, Past Simple, Past Continuous, Present Perfect, Present Perfect Continuous, Past Perfect, Past Perfect Continuous, Future Simple, Future Continuous, Future Perfect, Going To, Used To, Modal Verbs, Can/Can't, Have Got, Verb Be, Subject Pronouns, Articles, Prepositions, Comparatives, Superlatives, Conditionals, First Conditional, Second Conditional, Third Conditional, Reported Speech, Passive Voice, Relative Clauses, Wish Clauses, Infinitives and Gerunds, Adverbs of Frequency, Word Order, Phrasal Verbs, Mixed Grammar, Like/Love/Hate + -ing, Possessives, Plural Nouns

Return ONLY the JSON object, no other text."""

        parts.append(prompt)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=parts,
        )
        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as e:
        return {}


# ── PDF UPLOAD ────────────────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload_resource(
    level: str = Form(...),
    pdf: UploadFile = File(...),
    user=Depends(require_auth)
):
    # Sanitize filename slug from original name
    base = pdf.filename.lower().rsplit(".", 1)[0]
    safe_slug = "".join(c if c.isalnum() else "-" for c in base).strip("-")
    safe_slug = safe_slug[:60] or "resource"
    safe_name = f"{safe_slug}.pdf"
    base_name = safe_slug

    pdf_dest = os.path.join(PDF_DIR, safe_name)

    # Avoid filename collisions
    counter = 1
    while os.path.exists(pdf_dest):
        safe_name = f"{safe_slug}-{counter}.pdf"
        base_name = f"{safe_slug}-{counter}"
        pdf_dest = os.path.join(PDF_DIR, safe_name)
        counter += 1

    # Save PDF
    os.makedirs(PDF_DIR, exist_ok=True)
    content = await pdf.read()
    with open(pdf_dest, "wb") as f:
        f.write(content)

    # Convert pages to images
    os.makedirs(IMG_DIR, exist_ok=True)
    img_prefix = os.path.join(IMG_DIR, base_name)
    try:
        subprocess.run(
            ["pdftoppm", "-jpeg", "-r", "150", pdf_dest, img_prefix],
            check=True, capture_output=True
        )
    except Exception:
        pass

    page_files = sorted(glob.glob(f"{img_prefix}-*.jpg"))
    page_basenames = [os.path.basename(p) for p in page_files]

    # AI analysis
    ai = analyse_pages_with_ai(page_files, level)

    # Order pages: teacher notes last
    teacher_note_pages = set((ai.get("teacher_notes_pages") or []))
    ordered_pages = []
    notes_pages = []
    for i, bn in enumerate(page_basenames, 1):
        if i in teacher_note_pages:
            notes_pages.append(f"img/{bn}")
        else:
            ordered_pages.append(f"img/{bn}")
    ordered_pages.extend(notes_pages)

    thumbnail = ordered_pages[0] if ordered_pages else (f"img/{page_basenames[0]}" if page_basenames else "")
    grammar_list = ai.get("grammar") or []
    title = ai.get("title") or safe_slug.replace("-", " ").title()
    description = ai.get("description") or ""
    activity_type = ai.get("type") or "worksheet"

    now = now_iso()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO resources (title, description, level, grammar, type,
               thumbnail, pages, pdf_path, uploaded_by, status, submitted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (title, description, level, json.dumps(grammar_list), activity_type,
             thumbnail, json.dumps(ordered_pages),
             f"pdf/{safe_name}", user["user_id"], "pending", now)
        )
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {"ok": True, "id": rid, "status": "pending", "title": title, "pages": ordered_pages}


# ── MY SUBMISSIONS ─────────────────────────────────────────────────────────────
@app.get("/api/my-submissions")
def my_submissions(user=Depends(require_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM resources WHERE uploaded_by = ? AND status != 'approved' ORDER BY submitted_at DESC",
            (user["user_id"],)
        ).fetchall()
        results = [resource_to_dict(row) for row in rows]
    return results
