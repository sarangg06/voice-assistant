"""
jarvis_db.py
------------
MongoDB integration layer for Jarvis Voice Assistant.
Handles connection management and all CRUD operations for:
  - Command history / logs
  - Reminders
  - Personal notes
  - Contacts

All collections live in a single database called  "jarvis_db".
Connection URI is read from the MONGO_URI environment variable
(default: mongodb://localhost:27017/).
"""

import os
import datetime
from dotenv import load_dotenv

try:
    from pymongo import MongoClient, ASCENDING, DESCENDING
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False

load_dotenv()

# ──────────────────────────────────────────────
# Connection state (module-level singletons)
# ──────────────────────────────────────────────
_client = None
_db     = None

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "jarvis_db"

# Collection names
COL_HISTORY   = "command_history"
COL_REMINDERS = "reminders"
COL_NOTES     = "notes"
COL_CONTACTS  = "contacts"


# ──────────────────────────────────────────────
# Connection helpers
# ──────────────────────────────────────────────

def connect_db():
    """
    Open a MongoDB connection and return (client, db).
    Raises RuntimeError if pymongo is not installed or
    the server cannot be reached.
    """
    global _client, _db

    if not PYMONGO_AVAILABLE:
        raise RuntimeError(
            "pymongo is not installed. Run:  pip install pymongo"
        )

    if _client is not None:
        return _client, _db          # already connected

    try:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
        # Force an actual network round-trip to confirm the server is up.
        _client.admin.command("ping")
        _db = _client[DB_NAME]
        _ensure_indexes()
        print(f"[DB] Connected to MongoDB at  {MONGO_URI}  (database: {DB_NAME})")
        return _client, _db
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        _client = None
        _db     = None
        raise RuntimeError(
            f"[DB] Could not connect to MongoDB: {exc}\n"
            "Make sure mongod is running locally or update MONGO_URI in your .env file."
        ) from exc


def disconnect_db():
    """Close the MongoDB connection gracefully."""
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db     = None
        print("[DB] MongoDB connection closed.")


def get_db():
    """
    Return the active database handle, connecting first if needed.
    All feature functions call this so callers never worry about
    connection state.
    """
    if _db is None:
        connect_db()
    return _db


def check_db_health():
    """
    Ping the server and return a human-readable status string.
    Safe to call at any time — won't raise.
    """
    try:
        db = get_db()
        db.client.admin.command("ping")
        return "MongoDB is connected and healthy."
    except Exception as exc:
        return f"MongoDB is NOT reachable: {exc}"


def _ensure_indexes():
    """Create useful indexes once on first connection."""
    db = _db
    # Command history: fast retrieval by timestamp
    db[COL_HISTORY].create_index([("timestamp", DESCENDING)])
    # Reminders: query by due time and whether triggered
    db[COL_REMINDERS].create_index([("due_at", ASCENDING)])
    db[COL_REMINDERS].create_index([("triggered", ASCENDING)])
    # Notes: text search on content
    db[COL_NOTES].create_index([("content", "text"), ("title", "text")])
    # Contacts: case-insensitive name lookup
    db[COL_CONTACTS].create_index([("name", ASCENDING)])


# ──────────────────────────────────────────────
# 1.  Command history / logs
# ──────────────────────────────────────────────

def log_command(query: str, result_status: str = "ok"):
    """
    Automatically called by process_query() to record every
    voice command the user gives.

    Document shape:
        { query, result_status, timestamp }
    """
    try:
        db = get_db()
        db[COL_HISTORY].insert_one({
            "query":         query,
            "result_status": result_status,   # "ok" | "error" | "shutdown"
            "timestamp":     datetime.datetime.utcnow(),
        })
    except Exception as exc:
        print(f"[DB] Failed to log command: {exc}")


def get_recent_commands(limit: int = 10) -> list:
    """Return the N most recent commands as a list of dicts."""
    try:
        db = get_db()
        cursor = (
            db[COL_HISTORY]
            .find({}, {"_id": 0, "query": 1, "timestamp": 1, "result_status": 1})
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        return list(cursor)
    except Exception as exc:
        print(f"[DB] get_recent_commands error: {exc}")
        return []


def clear_command_history() -> int:
    """Delete all command history. Returns count deleted."""
    try:
        db = get_db()
        result = db[COL_HISTORY].delete_many({})
        return result.deleted_count
    except Exception as exc:
        print(f"[DB] clear_command_history error: {exc}")
        return 0


# ──────────────────────────────────────────────
# 2.  Reminders
# ──────────────────────────────────────────────

def save_reminder(text: str, due_at: datetime.datetime) -> str:
    """
    Persist a reminder.

    Document shape:
        { text, due_at, triggered, created_at }

    Returns the inserted document's string ID.
    """
    try:
        db = get_db()
        result = db[COL_REMINDERS].insert_one({
            "text":       text,
            "due_at":     due_at,
            "triggered":  False,
            "created_at": datetime.datetime.utcnow(),
        })
        return str(result.inserted_id)
    except Exception as exc:
        print(f"[DB] save_reminder error: {exc}")
        return ""


def get_pending_reminders() -> list:
    """Return all reminders that have not yet been triggered."""
    try:
        db = get_db()
        cursor = (
            db[COL_REMINDERS]
            .find({"triggered": False}, {"_id": 0, "text": 1, "due_at": 1})
            .sort("due_at", ASCENDING)
        )
        return list(cursor)
    except Exception as exc:
        print(f"[DB] get_pending_reminders error: {exc}")
        return []


def mark_reminder_triggered(text: str):
    """Mark the first matching untriggered reminder as done."""
    try:
        db = get_db()
        db[COL_REMINDERS].update_one(
            {"text": text, "triggered": False},
            {"$set": {"triggered": True}}
        )
    except Exception as exc:
        print(f"[DB] mark_reminder_triggered error: {exc}")


def delete_all_reminders() -> int:
    """Clear all reminders. Returns count deleted."""
    try:
        db = get_db()
        result = db[COL_REMINDERS].delete_many({})
        return result.deleted_count
    except Exception as exc:
        print(f"[DB] delete_all_reminders error: {exc}")
        return 0


# ──────────────────────────────────────────────
# 3.  Personal notes
# ──────────────────────────────────────────────

def save_note(title: str, content: str) -> str:
    """
    Save a personal note.

    Document shape:
        { title, content, created_at }

    Returns the inserted document's string ID.
    """
    try:
        db = get_db()
        result = db[COL_NOTES].insert_one({
            "title":      title,
            "content":    content,
            "created_at": datetime.datetime.utcnow(),
        })
        return str(result.inserted_id)
    except Exception as exc:
        print(f"[DB] save_note error: {exc}")
        return ""


def get_all_notes() -> list:
    """Return all saved notes, newest first."""
    try:
        db = get_db()
        cursor = (
            db[COL_NOTES]
            .find({}, {"_id": 0, "title": 1, "content": 1, "created_at": 1})
            .sort("created_at", DESCENDING)
        )
        return list(cursor)
    except Exception as exc:
        print(f"[DB] get_all_notes error: {exc}")
        return []


def find_note_by_keyword(keyword: str) -> list:
    """Full-text search across note titles and content."""
    try:
        db = get_db()
        cursor = db[COL_NOTES].find(
            {"$text": {"$search": keyword}},
            {"_id": 0, "title": 1, "content": 1}
        )
        return list(cursor)
    except Exception as exc:
        print(f"[DB] find_note_by_keyword error: {exc}")
        return []


def delete_note_by_title(title: str) -> int:
    """Delete all notes matching the given title. Returns count deleted."""
    try:
        db = get_db()
        result = db[COL_NOTES].delete_many(
            {"title": {"$regex": f"^{title}$", "$options": "i"}}
        )
        return result.deleted_count
    except Exception as exc:
        print(f"[DB] delete_note_by_title error: {exc}")
        return 0


# ──────────────────────────────────────────────
# 4.  Contacts
# ──────────────────────────────────────────────

def save_contact(name: str, phone: str = "", email: str = "") -> str:
    """
    Upsert a contact (update if name already exists, else insert).

    Document shape:
        { name, phone, email, updated_at }

    Returns the upserted/modified document's string ID or "".
    """
    try:
        db = get_db()
        result = db[COL_CONTACTS].update_one(
            {"name": {"$regex": f"^{name}$", "$options": "i"}},
            {
                "$set": {
                    "name":       name,
                    "phone":      phone,
                    "email":      email,
                    "updated_at": datetime.datetime.utcnow(),
                }
            },
            upsert=True,
        )
        uid = result.upserted_id or ""
        return str(uid)
    except Exception as exc:
        print(f"[DB] save_contact error: {exc}")
        return ""


def find_contact(name: str) -> dict | None:
    """
    Case-insensitive name lookup.
    Returns the contact dict (without _id) or None.
    """
    try:
        db = get_db()
        doc = db[COL_CONTACTS].find_one(
            {"name": {"$regex": name, "$options": "i"}},
            {"_id": 0, "name": 1, "phone": 1, "email": 1}
        )
        return doc
    except Exception as exc:
        print(f"[DB] find_contact error: {exc}")
        return None


def get_all_contacts() -> list:
    """Return every contact sorted alphabetically by name."""
    try:
        db = get_db()
        cursor = (
            db[COL_CONTACTS]
            .find({}, {"_id": 0, "name": 1, "phone": 1, "email": 1})
            .sort("name", ASCENDING)
        )
        return list(cursor)
    except Exception as exc:
        print(f"[DB] get_all_contacts error: {exc}")
        return []


def delete_contact(name: str) -> int:
    """Remove a contact by name (case-insensitive). Returns count deleted."""
    try:
        db = get_db()
        result = db[COL_CONTACTS].delete_one(
            {"name": {"$regex": f"^{name}$", "$options": "i"}}
        )
        return result.deleted_count
    except Exception as exc:
        print(f"[DB] delete_contact error: {exc}")
        return 0
