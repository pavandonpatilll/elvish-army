from fastapi import (
    Request,
    FastAPI,
    HTTPException,
    Header,
    File,
    UploadFile
)

from fastapi.staticfiles import StaticFiles

from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

import sqlite3
import secrets
import base64
import json
import logging
import re

from datetime import datetime

import os
import uuid


# =====================================================
# APP
# =====================================================

app = FastAPI(
    title="Elvish Army API",
    version="2.0.0"
)


# =====================================================
# UPLOADS
# =====================================================

UPLOAD_ROOT = "uploads"

UPLOAD_DIR = os.path.join(
    UPLOAD_ROOT,
    "community"
)

os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
)


# =====================================================
# STATIC FILES
# =====================================================

app.mount(
    "/uploads",
    StaticFiles(directory=UPLOAD_ROOT),
    name="uploads"
)


# =====================================================
# CORS
# =====================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================
# DATABASE
# =====================================================

DB_NAME = os.getenv("ELVISH_ARMY_DB_PATH", "elvish_army.db")

# Firebase Admin SDK credentials are supplied through Render Environment Variables.
# Supported: FIREBASE_SERVICE_ACCOUNT_BASE64, FIREBASE_SERVICE_ACCOUNT_JSON,
# or GOOGLE_APPLICATION_CREDENTIALS (file path).
logger = logging.getLogger("elvish_army.firebase")
FIRESTORE = None
FIREBASE_ENABLED = False
FIREBASE_LAST_ERROR = ""
FIRESTORE_SYNC_ENABLED = False

FIRESTORE_TABLES = (
    "updates", "news", "videos", "photos", "community_comments",
    "poll_votes", "users", "community_posts", "community_post_likes",
    "polls", "notifications", "app_stats",
    "social_profiles", "social_follows", "direct_messages", "social_reports"
)

def init_firebase():
    global FIRESTORE, FIREBASE_ENABLED, FIREBASE_LAST_ERROR
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            b64_value = os.getenv("FIREBASE_SERVICE_ACCOUNT_BASE64", "").strip()
            json_value = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
            file_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

            if b64_value:
                raw = base64.b64decode(b64_value).decode("utf-8")
                cred = credentials.Certificate(json.loads(raw))
            elif json_value:
                cred = credentials.Certificate(json.loads(json_value))
            elif file_path and os.path.isfile(file_path):
                cred = credentials.Certificate(file_path)
            else:
                FIREBASE_LAST_ERROR = "Firebase credentials are not configured"
                logger.warning(FIREBASE_LAST_ERROR)
                return

            firebase_admin.initialize_app(cred)

        FIRESTORE = firestore.client()
        FIREBASE_ENABLED = True
        FIREBASE_LAST_ERROR = ""
        logger.info("Firebase Firestore connected")
    except Exception as exc:
        FIREBASE_ENABLED = False
        FIRESTORE = None
        FIREBASE_LAST_ERROR = str(exc)
        logger.exception("Firebase initialization failed")

init_firebase()


# =====================================================
# ADMIN
# =====================================================

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "elvish123")


# =====================================================
# ADMIN TOKENS
# =====================================================

admin_tokens = set()

# =====================================================
# DATABASE
# =====================================================

class DatabaseConnection:
    """SQLite-compatible wrapper that mirrors modified tables to Firestore."""
    def __init__(self, connection):
        self._conn = connection
        self._changed_tables = set()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, *args, **kwargs):
        sql = args[0] if args else ""
        match = re.search(
            r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            str(sql),
            re.IGNORECASE
        )
        if match:
            self._changed_tables.add(match.group(1).lower())
        return self._conn.execute(*args, **kwargs)

    def commit(self):
        self._conn.commit()
        if FIREBASE_ENABLED and FIRESTORE_SYNC_ENABLED and self._changed_tables:
            changed = self._changed_tables.intersection(FIRESTORE_TABLES)
            try:
                if changed:
                    sync_sqlite_to_firestore(self._conn, changed)
            except Exception as exc:
                global FIREBASE_LAST_ERROR
                FIREBASE_LAST_ERROR = str(exc)
                logger.exception("Firestore sync failed after SQLite commit")
                raise RuntimeError("Firestore sync failed. Check Render logs and Firebase credentials.") from exc
            finally:
                self._changed_tables.clear()

    def close(self):
        self._conn.close()


def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    return DatabaseConnection(conn)


def _firestore_collection(table_name):
    return FIRESTORE.collection("elvish_army_" + table_name)


def sync_sqlite_to_firestore(connection, table_names=None):
    """Mirror SQLite tables to Firestore collections."""
    if not FIRESTORE:
        return
    global FIREBASE_LAST_ERROR
    selected_tables = table_names or FIRESTORE_TABLES
    for table_name in selected_tables:
        rows = connection.execute(f"SELECT * FROM {table_name}").fetchall()
        collection = _firestore_collection(table_name)
        existing = {doc.id: doc.to_dict() for doc in collection.stream()}
        current_ids = set()
        writes = []
        for row in rows:
            item = dict(row)
            doc_id = str(item.get("id"))
            current_ids.add(doc_id)
            if existing.get(doc_id) != item:
                writes.append(("set", doc_id, item))
        for doc_id in existing.keys() - current_ids:
            writes.append(("delete", doc_id, None))
        # Keep batches below Firestore's 500-operation limit.
        for start in range(0, len(writes), 400):
            batch = FIRESTORE.batch()
            for action, doc_id, item in writes[start:start + 400]:
                ref = collection.document(doc_id)
                if action == "delete":
                    batch.delete(ref)
                else:
                    batch.set(ref, item)
            batch.commit()
    FIREBASE_LAST_ERROR = ""


def restore_firestore_to_sqlite(connection):
    """Restore Firestore collections into the local SQLite cache on Render restart."""
    if not FIRESTORE:
        return
    for table_name in FIRESTORE_TABLES:
        documents = list(_firestore_collection(table_name).stream())
        if not documents:
            continue
        rows = [doc.to_dict() for doc in documents]
        if not rows:
            continue
        columns = list(rows[0].keys())
        # These are app-owned collections; replace the local table with its cloud copy.
        connection.execute(f"DELETE FROM {table_name}")
        placeholders = ",".join("?" for _ in columns)
        column_sql = ",".join('"' + col.replace('"', '""') + '"' for col in columns)
        sql = f"INSERT OR REPLACE INTO {table_name} ({column_sql}) VALUES ({placeholders})"
        for row in rows:
            connection.execute(sql, [row.get(col) for col in columns])
    connection.commit()


def firebase_startup_sync():
    global FIRESTORE_SYNC_ENABLED, FIREBASE_LAST_ERROR
    if not FIREBASE_ENABLED:
        return
    try:
        raw = sqlite3.connect(DB_NAME, timeout=30)
        raw.row_factory = sqlite3.Row
        restore_firestore_to_sqlite(raw)
        # Initial deployment migrates existing SQLite rows; subsequent restarts
        # restore cloud records before enabling normal mirroring.
        sync_sqlite_to_firestore(raw)
        raw.close()
        FIRESTORE_SYNC_ENABLED = True
        FIREBASE_LAST_ERROR = ""
        logger.info("Firestore restore/mirror ready")
    except Exception as exc:
        FIRESTORE_SYNC_ENABLED = False
        FIREBASE_LAST_ERROR = str(exc)
        logger.exception("Firestore startup sync failed")


@app.get("/api/firebase/status")
def firebase_status():
    return {
        "status": "connected" if FIREBASE_ENABLED and not FIREBASE_LAST_ERROR else "not_connected",
        "firebase_enabled": FIREBASE_ENABLED,
        "firestore_sync_enabled": FIRESTORE_SYNC_ENABLED,
        "message": "Firestore connected and syncing" if FIREBASE_ENABLED and FIRESTORE_SYNC_ENABLED and not FIREBASE_LAST_ERROR else (FIREBASE_LAST_ERROR or "Firebase is not configured")
    }


# =====================================================
# MAIN DATABASE TABLES
# =====================================================

def init_db():

    conn = get_db()

    # ---------------------------------------------
    # UPDATES
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            time TEXT NOT NULL,
            likes INTEGER DEFAULT 0
        )
    """)

    # ---------------------------------------------
    # NEWS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            date TEXT NOT NULL
        )
    """)

    # ---------------------------------------------
    # VIDEOS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            thumbnail TEXT DEFAULT ''
        )
    """)

    # ---------------------------------------------
    # PHOTOS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            image_url TEXT NOT NULL
        )
    """)

# ==================================================
# COMMUNITY COMMENTS
# ==================================================

    conn.execute("""
    CREATE TABLE IF NOT EXISTS community_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        username TEXT NOT NULL,
        comment TEXT NOT NULL,
        parent_id INTEGER DEFAULT NULL,
        likes INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
        )
    """)

    # ---------------------------------------------
    # POLL VOTES
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poll_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id INTEGER NOT NULL,
            voter_id TEXT NOT NULL,
            option INTEGER NOT NULL,
            UNIQUE(poll_id, voter_id)
        )
    """)

    conn.commit()

    conn.close()


# =====================================================
# EXTRA DATABASE TABLES
# =====================================================

def init_extra_db():

    conn = get_db()

    # ---------------------------------------------
    # USERS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)

    # ---------------------------------------------
    # COMMUNITY POSTS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS community_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            image_url TEXT DEFAULT '',
            likes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # COMMUNITY POST LIKES
    conn.execute("""
    CREATE TABLE IF NOT EXISTS community_post_likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(post_id, user_id)
        )
    """)

    # ---------------------------------------------
    # POLLS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            option1 TEXT NOT NULL,
            option2 TEXT NOT NULL,
            votes1 INTEGER DEFAULT 0,
            votes2 INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    # ---------------------------------------------
    # NOTIFICATIONS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # ---------------------------------------------
    # APP STATS
    # ---------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_stats (
            id INTEGER PRIMARY KEY,
            views INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        INSERT OR IGNORE INTO app_stats
        (id, views)
        VALUES (1, 0)
    """)

    conn.commit()

    conn.close()


# ==================================================
# COMMUNITY POSTS MIGRATION - FINAL
# ==================================================

def migrate_community_posts():

    conn = get_db()

    columns = conn.execute(
        "PRAGMA table_info(community_posts)"
    ).fetchall()

    column_names = [
        column["name"]
        for column in columns
    ]

    # user_id
    if "user_id" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN user_id TEXT DEFAULT ''
        """)

    # username
    if "username" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN username TEXT DEFAULT 'Elvish Army'
        """)

    # content
    if "content" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN content TEXT DEFAULT ''
        """)

    # image_url
    if "image_url" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN image_url TEXT DEFAULT ''
        """)

    # media_url
    if "media_url" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN media_url TEXT DEFAULT ''
        """)

    # media_type
    if "media_type" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN media_type TEXT DEFAULT ''
        """)

    # likes
    if "likes" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN likes INTEGER DEFAULT 0
        """)

    # comments
    if "comments" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN comments INTEGER DEFAULT 0
        """)

    # created_at
    if "created_at" not in column_names:
        conn.execute("""
            ALTER TABLE community_posts
            ADD COLUMN created_at TEXT DEFAULT ''
        """)

    # Old image_url -> media_url
    conn.execute("""
        UPDATE community_posts
        SET media_url = image_url
        WHERE
            (media_url IS NULL OR media_url = '')
            AND image_url IS NOT NULL
            AND image_url != ''
    """)

    # Old images ko image type do
    conn.execute("""
        UPDATE community_posts
        SET media_type = 'image/jpeg'
        WHERE
            media_url != ''
            AND (media_type IS NULL OR media_type = '')
    """)

    conn.commit()
    conn.close()

# ==================================================
# COMMUNITY COMMENTS MIGRATION
# ==================================================

def migrate_community_comments():

    conn = get_db()

    # Existing columns check
    columns = conn.execute(
        "PRAGMA table_info(community_comments)"
    ).fetchall()

    column_names = [
        column["name"]
        for column in columns
    ]

    # ----------------------------------------------
    # parent_id
    # ----------------------------------------------

    if "parent_id" not in column_names:

        conn.execute("""
            ALTER TABLE community_comments
            ADD COLUMN parent_id INTEGER DEFAULT NULL
        """)

    # ----------------------------------------------
    # likes
    # ----------------------------------------------

    if "likes" not in column_names:

        conn.execute("""
            ALTER TABLE community_comments
            ADD COLUMN likes INTEGER DEFAULT 0
        """)

    conn.commit()
    conn.close()

# =====================================================
# DATABASE STARTUP ORDER
# =====================================================

init_db()

init_extra_db()

migrate_community_posts()

migrate_community_comments()

# Instagram-style social features (profiles, follows, DMs, reports).
def init_social_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS social_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL,
        bio TEXT DEFAULT '',
        avatar_url TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS social_follows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        follower_id TEXT NOT NULL,
        following_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(follower_id, following_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS direct_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id TEXT NOT NULL,
        sender_name TEXT NOT NULL,
        recipient_id TEXT NOT NULL,
        body TEXT DEFAULT '',
        media_url TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        read_at TEXT DEFAULT ''
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS social_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()

init_social_db()

# Run after all tables/migrations exist. If credentials are not configured,
# the existing SQLite-only behavior remains available.
firebase_startup_sync()

# =====================================================
# MODELS
# =====================================================

class LoginRequest(BaseModel):
    username: str
    password: str


class UpdateCreate(BaseModel):
    title: str
    description: str


class NewsCreate(BaseModel):
    title: str
    description: str


class VideoCreate(BaseModel):
    title: str
    url: str
    thumbnail: str = ""


class PhotoCreate(BaseModel):
    title: str
    image_url: str


class CommunityCommentCreate(BaseModel):

    user_id: str = "guest"
    username: str = "Guest"
    comment: str
    parent_id: int | None = None

class CommunityLikeCreate(BaseModel):
    user_id: str

# =====================================================
# EXTRA MODELS
# =====================================================

class PollCreate(BaseModel):
    question: str
    option1: str
    option2: str


class CommunityPostCreate(BaseModel):

    user_id: str

    username: str

    content: str

    media_url: str = ""

    media_type: str = ""


class NotificationCreate(BaseModel):
    title: str
    message: str


class UserCreate(BaseModel):
    username: str

class PollVote(BaseModel):

    option: int

# =====================================================
# ADMIN AUTH
# =====================================================

def check_admin(token):

    if not token or token not in admin_tokens:
        raise HTTPException(
            status_code=401,
            detail="Admin login required"
        )


@app.post("/api/admin/login")
def admin_login(data: LoginRequest):

    if (
        data.username == ADMIN_USERNAME
        and data.password == ADMIN_PASSWORD
    ):

        token = secrets.token_urlsafe(32)

        admin_tokens.add(token)

        return {
            "status": "success",
            "message": "Login successful 🔐",
            "token": token
        }

    raise HTTPException(
        status_code=401,
        detail="Wrong username or password"
    )


@app.post("/api/admin/logout")
def admin_logout(
    authorization: str | None = Header(default=None)
):

    token = None

    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    if token:
        admin_tokens.discard(token)

    return {
        "status": "success",
        "message": "Logged out"
    }


# =====================================================
# HOME
# =====================================================

@app.get("/")
def home():

    return {
        "status": "success",
        "message": "Elvish Army API is running 🔥"
    }


# =====================================================
# UPDATES
# =====================================================

@app.get("/api/updates")
def get_updates():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM updates
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "status": "success",
        "updates": [dict(row) for row in rows]
    }


@app.post("/api/updates")
def add_update(
    update: UpdateCreate,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO updates
        (title, description, time, likes)
        VALUES (?, ?, ?, ?)
    """, (
        update.title,
        update.description,
        "Just now",
        0
    ))

    conn.commit()

    update_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "id": update_id
    }


@app.delete("/api/updates/{update_id}")
def delete_update(
    update_id: int,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute(
        "DELETE FROM updates WHERE id = ?",
        (update_id,)
    )

    conn.commit()

    deleted = cursor.rowcount

    conn.close()

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail="Update not found"
        )

    return {
        "status": "success",
        "message": "Update deleted"
    }


def check_admin_token(authorization):

    token = None

    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    check_admin(token)


# =====================================================
# NEWS
# =====================================================

@app.get("/api/news")
def get_news():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM news
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "status": "success",
        "news": [dict(row) for row in rows]
    }


@app.post("/api/news")
def add_news(
    news: NewsCreate,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO news
        (title, description, date)
        VALUES (?, ?, ?)
    """, (
        news.title,
        news.description,
        datetime.now().strftime("%Y-%m-%d")
    ))

    conn.commit()

    news_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": "News added 📰",
        "id": news_id
    }


@app.put("/api/news/{news_id}")
def edit_news(
    news_id: int,
    news: NewsCreate,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute("""
        UPDATE news
        SET title = ?, description = ?
        WHERE id = ?
    """, (
        news.title,
        news.description,
        news_id
    ))

    conn.commit()

    updated = cursor.rowcount

    conn.close()

    if updated == 0:
        raise HTTPException(
            status_code=404,
            detail="News not found"
        )

    return {
        "status": "success",
        "message": "News updated"
    }


@app.delete("/api/news/{news_id}")
def delete_news(
    news_id: int,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute(
        "DELETE FROM news WHERE id = ?",
        (news_id,)
    )

    conn.commit()

    deleted = cursor.rowcount

    conn.close()

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail="News not found"
        )

    return {
        "status": "success",
        "message": "News deleted"
    }


# =====================================================
# VIDEOS
# =====================================================

@app.get("/api/videos")
def get_videos():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM videos
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "status": "success",
        "videos": [dict(row) for row in rows]
    }


@app.post("/api/videos")
def add_video(
    video: VideoCreate,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO videos
        (title, url, thumbnail)
        VALUES (?, ?, ?)
    """, (
        video.title,
        video.url,
        video.thumbnail
    ))

    conn.commit()

    video_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": "Video added 🎬",
        "id": video_id
    }


@app.delete("/api/videos/{video_id}")
def delete_video(
    video_id: int,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute(
        "DELETE FROM videos WHERE id = ?",
        (video_id,)
    )

    conn.commit()

    deleted = cursor.rowcount

    conn.close()

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail="Video not found"
        )

    return {
        "status": "success",
        "message": "Video deleted"
    }


# =====================================================
# PHOTOS
# =====================================================

@app.get("/api/photos")
def get_photos():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM photos
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "status": "success",
        "photos": [dict(row) for row in rows]
    }


@app.post("/api/photos")
def add_photo(
    photo: PhotoCreate,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO photos
        (title, image_url)
        VALUES (?, ?)
    """, (
        photo.title,
        photo.image_url
    ))

    conn.commit()

    photo_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": "Photo added 📸",
        "id": photo_id
    }


@app.delete("/api/photos/{photo_id}")
def delete_photo(
    photo_id: int,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute(
        "DELETE FROM photos WHERE id = ?",
        (photo_id,)
    )

    conn.commit()

    deleted = cursor.rowcount

    conn.close()

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail="Photo not found"
        )

    return {
        "status": "success",
        "message": "Photo deleted"
    }

# =====================================================
# POLLS
# =====================================================

@app.get("/api/polls")
def get_polls():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM polls
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "status": "success",
        "polls": [dict(row) for row in rows]
    }


@app.post("/api/polls")
def create_poll(
    poll: PollCreate,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO polls
        (question, option1, option2, votes1, votes2, created_at)
        VALUES (?, ?, ?, 0, 0, ?)
    """, (
        poll.question,
        poll.option1,
        poll.option2,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    conn.commit()

    poll_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": "Poll created 🗳️",
        "id": poll_id
    }


@app.post("/api/polls/{poll_id}/vote/{option}")
def vote_poll(
    poll_id: int,
    option: int,
    request: Request
):

    if option not in [1, 2]:

        raise HTTPException(
            status_code=400,
            detail="Invalid option"
        )

    voter_id = request.client.host

    conn = get_db()

    # Check poll
    poll = conn.execute(
        """
        SELECT id, votes1, votes2
        FROM polls
        WHERE id = ?
        """,
        (poll_id,)
    ).fetchone()

    if not poll:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Poll not found"
        )

    # Check existing vote
    old_vote = conn.execute(
        """
        SELECT id, option
        FROM poll_votes
        WHERE poll_id = ?
        AND voter_id = ?
        """,
        (
            poll_id,
            voter_id
        )
    ).fetchone()


    # ==========================================
    # SAME OPTION CLICKED AGAIN
    # ==========================================

    if old_vote and old_vote["option"] == option:

        conn.close()

        return {
            "status": "success",
            "message": "Aap already isi option ko vote kar chuke ho 🗳️",
            "changed": False
        }


    # ==========================================
    # USER CHANGED VOTE
    # ==========================================

    if old_vote:

        old_option = old_vote["option"]

        # Remove old vote count
        if old_option == 1:

            conn.execute(
                """
                UPDATE polls
                SET votes1 = MAX(votes1 - 1, 0)
                WHERE id = ?
                """,
                (poll_id,)
            )

        else:

            conn.execute(
                """
                UPDATE polls
                SET votes2 = MAX(votes2 - 1, 0)
                WHERE id = ?
                """,
                (poll_id,)
            )


        # Update user's vote
        conn.execute(
            """
            UPDATE poll_votes
            SET option = ?
            WHERE poll_id = ?
            AND voter_id = ?
            """,
            (
                option,
                poll_id,
                voter_id
            )
        )


    # ==========================================
    # FIRST TIME VOTE
    # ==========================================

    else:

        conn.execute(
            """
            INSERT INTO poll_votes
            (
                poll_id,
                voter_id,
                option
            )
            VALUES (?, ?, ?)
            """,
            (
                poll_id,
                voter_id,
                option
            )
        )


    # ==========================================
    # ADD NEW VOTE
    # ==========================================

    if option == 1:

        conn.execute(
            """
            UPDATE polls
            SET votes1 = votes1 + 1
            WHERE id = ?
            """,
            (poll_id,)
        )

    else:

        conn.execute(
            """
            UPDATE polls
            SET votes2 = votes2 + 1
            WHERE id = ?
            """,
            (poll_id,)
        )


    conn.commit()

    # Get latest result
    result = conn.execute(
        """
        SELECT votes1, votes2
        FROM polls
        WHERE id = ?
        """,
        (poll_id,)
    ).fetchone()

    conn.close()


    return {
        "status": "success",
        "message": (
            "Vote change ho gaya 🔄🗳️"
            if old_vote
            else
            "Vote recorded 🗳️🔥"
        ),
        "changed": bool(old_vote),
        "votes1": result["votes1"],
        "votes2": result["votes2"]
    }


@app.delete("/api/polls/{poll_id}")
def delete_poll(
    poll_id: int,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute(
        "DELETE FROM polls WHERE id = ?",
        (poll_id,)
    )

    conn.commit()

    deleted = cursor.rowcount

    conn.close()

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail="Poll not found"
        )

    return {
        "status": "success",
        "message": "Poll deleted"
    }


# ==================================================
# GET COMMUNITY POSTS
# ==================================================

@app.get("/api/community")
def get_community(
    user_id: str | None = None
):

    conn = get_db()

    rows = conn.execute("""
        SELECT
            p.id,
            p.user_id,
            p.username,
            p.content,
            p.image_url,
            p.media_url,
            p.media_type,
            p.likes,
            p.comments,
            p.created_at,

            CASE
                WHEN l.id IS NOT NULL
                THEN 1
                ELSE 0
            END AS liked

        FROM community_posts p

        LEFT JOIN community_post_likes l
            ON p.id = l.post_id
            AND l.user_id = ?

        ORDER BY p.id DESC

    """, (
        user_id or "",
    )).fetchall()

    conn.close()

    return {
        "status": "success",
        "posts": [
            dict(row)
            for row in rows
        ]
    }
# ==================================================
# CREATE COMMUNITY POST
# ==================================================

@app.post("/api/community")
def create_community_post(
    post: CommunityPostCreate
):

    # Text ya media me se kam se kam ek hona chahiye
    if not post.content.strip() and not post.media_url:

        raise HTTPException(
            status_code=400,
            detail="Post me text ya photo/video hona chahiye"
        )

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO community_posts
        (
            user_id,
            username,
            content,
            image_url,
            media_url,
            media_type,
            likes,
            comments,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
    """, (
        post.user_id,
        post.username,
        post.content,
        post.media_url if post.media_type.startswith("image/") else "",
        post.media_url,
        post.media_type,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    _ensure_social_profile(conn, post.user_id, post.username)
    conn.commit()

    post_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": "Community post successfully published 🔥",
        "id": post_id
    }


# ==================================================
# COMMUNITY LIKE / UNLIKE
# ==================================================

@app.post("/api/community/{post_id}/like")
def like_community_post(
    post_id: int,
    data: CommunityLikeCreate
):

    conn = get_db()

    # Check post
    post = conn.execute("""
        SELECT id
        FROM community_posts
        WHERE id = ?
    """, (post_id,)).fetchone()

    if not post:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Post not found"
        )

    # Check whether this user already liked
    existing = conn.execute("""
        SELECT id
        FROM community_post_likes
        WHERE post_id = ?
        AND user_id = ?
    """, (
        post_id,
        data.user_id
    )).fetchone()

    # ==========================================
    # UNLIKE
    # ==========================================

    if existing:

        conn.execute("""
            DELETE FROM community_post_likes
            WHERE post_id = ?
            AND user_id = ?
        """, (
            post_id,
            data.user_id
        ))

        conn.execute("""
            UPDATE community_posts
            SET likes = CASE
                WHEN likes > 0 THEN likes - 1
                ELSE 0
            END
            WHERE id = ?
        """, (post_id,))

        liked = False

    # ==========================================
    # LIKE
    # ==========================================

    else:

        conn.execute("""
            INSERT INTO community_post_likes
            (
                post_id,
                user_id,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            post_id,
            data.user_id,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            )
        ))

        conn.execute("""
            UPDATE community_posts
            SET likes = likes + 1
            WHERE id = ?
        """, (post_id,))

        liked = True

    conn.commit()

    # Get latest count
    row = conn.execute("""
        SELECT likes
        FROM community_posts
        WHERE id = ?
    """, (post_id,)).fetchone()

    conn.close()

    return {
        "status": "success",
        "liked": liked,
        "likes": row["likes"]
    }

# ==================================================
# ADD COMMUNITY COMMENT / REPLY
# ==================================================

@app.post("/api/community/{post_id}/comment")
def add_community_comment(
    post_id: int,
    data: CommunityCommentCreate
):

    if not data.comment or not data.comment.strip():

        raise HTTPException(
            status_code=400,
            detail="Comment empty nahi ho sakta"
        )

    conn = get_db()

    # Check post
    post = conn.execute("""
        SELECT id
        FROM community_posts
        WHERE id = ?
    """, (post_id,)).fetchone()

    if not post:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Post not found"
        )

    # If reply, check parent comment
    if data.parent_id is not None:

        parent = conn.execute("""
            SELECT id
            FROM community_comments
            WHERE id = ?
            AND post_id = ?
        """, (
            data.parent_id,
            post_id
        )).fetchone()

        if not parent:

            conn.close()

            raise HTTPException(
                status_code=404,
                detail="Parent comment not found"
            )

    # Insert comment / reply
    cursor = conn.execute("""
        INSERT INTO community_comments
        (
            post_id,
            user_id,
            username,
            comment,
            parent_id,
            likes,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 0, ?)
    """, (
        post_id,
        data.user_id,
        data.username,
        data.comment.strip(),
        data.parent_id,
        datetime.now().strftime(
            "%Y-%m-%d %H:%M"
        )
    ))

    # Increase post comment count
    conn.execute("""
        UPDATE community_posts
        SET comments = comments + 1
        WHERE id = ?
    """, (post_id,))

    conn.commit()

    comment_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": (
            "Reply added 🔥"
            if data.parent_id is not None
            else "Comment added 💬"
        ),
        "id": comment_id
    }


# ==================================================
# GET COMMUNITY COMMENTS + REPLIES
# ==================================================

@app.get("/api/community/{post_id}/comments")
def get_community_comments(post_id: int):

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            post_id,
            user_id,
            username,
            comment,
            parent_id,
            likes,
            created_at
        FROM community_comments
        WHERE post_id = ?
        ORDER BY id ASC
    """, (post_id,)).fetchall()

    conn.close()

    return {
        "status": "success",
        "comments": [
            dict(row)
            for row in rows
        ]
    }

# ==================================================
# GET COMMUNITY COMMENTS
# ==================================================

@app.get("/api/community/{post_id}/comments")
def get_community_comments(post_id: int):

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            user_id,
            username,
            comment,
            created_at
        FROM community_comments
        WHERE post_id = ?
        ORDER BY id DESC
    """, (post_id,)).fetchall()

    conn.close()

    return {
        "status": "success",
        "comments": [dict(row) for row in rows]
    }

# ==================================================
# DELETE COMMUNITY POST — ADMIN ONLY
# ==================================================

@app.delete("/api/community/{post_id}")
def delete_community_post(
    post_id: int,
    authorization: str | None = Header(
        default=None
    )
):

    # Admin authentication
    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute(
        """
        DELETE FROM community_posts
        WHERE id = ?
        """,
        (post_id,)
    )

    conn.commit()

    deleted = cursor.rowcount

    conn.close()

    if deleted == 0:

        raise HTTPException(
            status_code=404,
            detail="Post not found"
        )

    return {
        "status": "success",
        "message": "Community post deleted 🗑️"
    }


# ==================================================
# api community uploads
# ==================================================


UPLOAD_DIR = "uploads/community"

os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
)

@app.post("/api/community/upload")
async def upload_community_media(
    file: UploadFile = File(...)
):

    if not file.content_type:

        raise HTTPException(
            status_code=400,
            detail="Invalid file"
        )

    allowed_types = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "video/mp4",
        "video/webm",
        "video/quicktime"
    ]

    if file.content_type not in allowed_types:

        raise HTTPException(
            status_code=400,
            detail="Only image/video files allowed"
        )

    extension = ""

    if file.filename and "." in file.filename:

        extension = "." + file.filename.rsplit(
            ".",
            1
        )[1].lower()

    filename = (
        str(uuid.uuid4())
        + extension
    )

    filepath = os.path.join(
        UPLOAD_DIR,
        filename
    )

    with open(filepath, "wb") as buffer:

        while True:

            chunk = await file.read(1024 * 1024)

            if not chunk:
                break

            buffer.write(chunk)

    return {
        "status": "success",
        "url": f"/uploads/community/{filename}",
        "filename": filename,
        "content_type": file.content_type
    }


# =====================================================
# NOTIFICATIONS
# =====================================================

@app.get("/api/notifications")
def get_notifications():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM notifications
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return {
        "status": "success",
        "notifications": [dict(row) for row in rows]
    }


@app.post("/api/notifications")
def create_notification(
    notification: NotificationCreate,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute("""
        INSERT INTO notifications
        (title, message, created_at)
        VALUES (?, ?, ?)
    """, (
        notification.title,
        notification.message,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    conn.commit()

    notification_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": "Notification sent 🔔",
        "id": notification_id
    }


@app.delete("/api/notifications/{notification_id}")
def delete_notification(
    notification_id: int,
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    cursor = conn.execute(
        "DELETE FROM notifications WHERE id = ?",
        (notification_id,)
    )

    conn.commit()

    deleted = cursor.rowcount

    conn.close()

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail="Notification not found"
        )

    return {
        "status": "success",
        "message": "Notification deleted"
    }

# =====================================================
# DASHBOARD
# =====================================================

@app.get("/api/admin/dashboard")
def dashboard(
    authorization: str | None = Header(default=None)
):

    check_admin_token(authorization)

    conn = get_db()

    updates = conn.execute(
        "SELECT COUNT(*) FROM updates"
    ).fetchone()[0]

    news = conn.execute(
        "SELECT COUNT(*) FROM news"
    ).fetchone()[0]

    videos = conn.execute(
        "SELECT COUNT(*) FROM videos"
    ).fetchone()[0]

    photos = conn.execute(
        "SELECT COUNT(*) FROM photos"
    ).fetchone()[0]

    polls = conn.execute(
        "SELECT COUNT(*) FROM polls"
    ).fetchone()[0]

    community = conn.execute(
        "SELECT COUNT(*) FROM community_posts"
    ).fetchone()[0]

    notifications = conn.execute(
        "SELECT COUNT(*) FROM notifications"
    ).fetchone()[0]

    users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    views = conn.execute(
        "SELECT views FROM app_stats WHERE id = 1"
    ).fetchone()[0]

    conn.close()

    return {
        "status": "success",

        "stats": {
            "users": users,
            "views": views,
            "updates": updates,
            "news": news,
            "videos": videos,
            "photos": photos,
            "polls": polls,
            "community_posts": community,
            "notifications": notifications
        }
    }


# =====================================================
# APP VIEW COUNTER
# =====================================================

@app.post("/api/app/view")
def add_view():

    conn = get_db()

    conn.execute("""
        UPDATE app_stats
        SET views = views + 1
        WHERE id = 1
    """)

    conn.commit()

    conn.close()

    return {
        "status": "success"
    }


# =====================================================
# REGISTER USER
# =====================================================

@app.post("/api/users")
def register_user(user: UserCreate):

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO users
            (username, created_at)
            VALUES (?, ?)
        """, (
            user.username,
            datetime.now().strftime("%Y-%m-%d %H:%M")
        ))

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        return {
            "status": "exists"
        }

    conn.close()

    return {
        "status": "success",
        "message": "User registered"
    }

    


# =====================================================
# SOCIAL PROFILES / FOLLOW GRAPH / DIRECT MESSAGES
# =====================================================
class SocialProfileUpdate(BaseModel):
    user_id: str
    username: str
    bio: str = ""
    avatar_url: str = ""

class FollowAction(BaseModel):
    follower_id: str
    following_id: str

class DirectMessageCreate(BaseModel):
    sender_id: str
    sender_name: str
    recipient_id: str
    body: str = ""
    media_url: str = ""
    media_type: str = ""

class SocialReportCreate(BaseModel):
    reporter_id: str
    target_type: str
    target_id: str
    reason: str


def _ensure_social_profile(conn, user_id: str, username: str = "Elvish Fan"):
    user_id = (user_id or "").strip()[:100]
    username = (username or "Elvish Fan").strip()[:30] or "Elvish Fan"
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID missing")
    existing = conn.execute("SELECT id FROM social_profiles WHERE user_id = ?", (user_id,)).fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if existing:
        conn.execute("UPDATE social_profiles SET username = ?, updated_at = ? WHERE user_id = ?", (username, now, user_id))
    else:
        conn.execute("INSERT INTO social_profiles (user_id, username, bio, avatar_url, created_at, updated_at) VALUES (?, ?, '', '', ?, ?)", (user_id, username, now, now))


def _profile_payload(conn, user_id: str, viewer_id: str = ""):
    profile = conn.execute("SELECT * FROM social_profiles WHERE user_id = ?", (user_id,)).fetchone()
    if not profile:
        return None
    posts_count = conn.execute("SELECT COUNT(*) AS n FROM community_posts WHERE user_id = ?", (user_id,)).fetchone()["n"]
    followers = conn.execute("SELECT COUNT(*) AS n FROM social_follows WHERE following_id = ?", (user_id,)).fetchone()["n"]
    following = conn.execute("SELECT COUNT(*) AS n FROM social_follows WHERE follower_id = ?", (user_id,)).fetchone()["n"]
    is_following = 0
    if viewer_id:
        is_following = 1 if conn.execute("SELECT id FROM social_follows WHERE follower_id = ? AND following_id = ?", (viewer_id, user_id)).fetchone() else 0
    return {"user_id": profile["user_id"], "username": profile["username"], "bio": profile["bio"] or "", "avatar_url": profile["avatar_url"] or "", "posts_count": posts_count, "followers_count": followers, "following_count": following, "is_following": bool(is_following), "created_at": profile["created_at"]}


@app.put("/api/social/profile")
def save_social_profile(data: SocialProfileUpdate):
    user_id = data.user_id.strip()[:100]
    username = data.username.strip()[:30]
    if not user_id or not username:
        raise HTTPException(status_code=400, detail="User ID aur username required hai")
    conn = get_db()
    _ensure_social_profile(conn, user_id, username)
    conn.execute("UPDATE social_profiles SET username = ?, bio = ?, avatar_url = ?, updated_at = ? WHERE user_id = ?", (username, data.bio.strip()[:160], data.avatar_url.strip()[:500], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))
    conn.commit()
    result = _profile_payload(conn, user_id, user_id)
    conn.close()
    return {"status": "success", "profile": result}


@app.get("/api/social/profile/{user_id}")
def get_social_profile(user_id: str, viewer_id: str = "", username: str = ""):
    conn = get_db()
    if not conn.execute("SELECT id FROM social_profiles WHERE user_id = ?", (user_id,)).fetchone() and username:
        _ensure_social_profile(conn, user_id, username)
        conn.commit()
    result = _profile_payload(conn, user_id, viewer_id)
    if result is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Profile nahi mila")
    conn.close()
    return {"status": "success", "profile": result}


@app.get("/api/social/search")
def search_social_users(q: str = "", viewer_id: str = ""):
    query = q.strip()[:60]
    conn = get_db()
    if query:
        rows = conn.execute("SELECT user_id FROM social_profiles WHERE username LIKE ? OR bio LIKE ? ORDER BY updated_at DESC LIMIT 40", (f"%{query}%", f"%{query}%")).fetchall()
    else:
        rows = conn.execute("SELECT user_id FROM social_profiles ORDER BY updated_at DESC LIMIT 40").fetchall()
    users = []
    for row in rows:
        item = _profile_payload(conn, row["user_id"], viewer_id)
        if item:
            users.append(item)
    conn.close()
    return {"status": "success", "users": users}


@app.post("/api/social/follow")
def toggle_social_follow(data: FollowAction):
    follower_id = data.follower_id.strip()[:100]
    following_id = data.following_id.strip()[:100]
    if not follower_id or not following_id or follower_id == following_id:
        raise HTTPException(status_code=400, detail="Khud ko follow nahi kar sakte")
    conn = get_db()
    if not conn.execute("SELECT id FROM social_profiles WHERE user_id = ?", (follower_id,)).fetchone():
        _ensure_social_profile(conn, follower_id, "Elvish Fan")
    if not conn.execute("SELECT id FROM social_profiles WHERE user_id = ?", (following_id,)).fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="User profile nahi mila")
    existing = conn.execute("SELECT id FROM social_follows WHERE follower_id = ? AND following_id = ?", (follower_id, following_id)).fetchone()
    if existing:
        conn.execute("DELETE FROM social_follows WHERE follower_id = ? AND following_id = ?", (follower_id, following_id))
        following = False
    else:
        conn.execute("INSERT INTO social_follows (follower_id, following_id, created_at) VALUES (?, ?, ?)", (follower_id, following_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        following = True
    conn.commit()
    result = _profile_payload(conn, following_id, follower_id)
    conn.close()
    return {"status": "success", "following": following, "profile": result}


@app.get("/api/social/followers/{user_id}")
def get_social_followers(user_id: str, viewer_id: str = ""):
    conn = get_db()
    rows = conn.execute("SELECT follower_id AS uid FROM social_follows WHERE following_id = ? ORDER BY id DESC LIMIT 500", (user_id,)).fetchall()
    users = [p for r in rows if (p := _profile_payload(conn, r["uid"], viewer_id))]
    conn.close()
    return {"status": "success", "users": users}


@app.get("/api/social/following/{user_id}")
def get_social_following(user_id: str, viewer_id: str = ""):
    conn = get_db()
    rows = conn.execute("SELECT following_id AS uid FROM social_follows WHERE follower_id = ? ORDER BY id DESC LIMIT 500", (user_id,)).fetchall()
    users = [p for r in rows if (p := _profile_payload(conn, r["uid"], viewer_id))]
    conn.close()
    return {"status": "success", "users": users}


@app.get("/api/social/feed")
def get_social_feed(user_id: str = "", following_only: bool = False):
    conn = get_db()
    if following_only and user_id:
        rows = conn.execute("""SELECT p.*, CASE WHEN l.id IS NOT NULL THEN 1 ELSE 0 END AS liked
            FROM community_posts p LEFT JOIN community_post_likes l ON p.id=l.post_id AND l.user_id=?
            WHERE p.user_id IN (SELECT following_id FROM social_follows WHERE follower_id=?) OR p.user_id=?
            ORDER BY p.id DESC LIMIT 100""", (user_id, user_id, user_id)).fetchall()
    else:
        rows = conn.execute("""SELECT p.*, CASE WHEN l.id IS NOT NULL THEN 1 ELSE 0 END AS liked
            FROM community_posts p LEFT JOIN community_post_likes l ON p.id=l.post_id AND l.user_id=?
            ORDER BY p.id DESC LIMIT 100""", (user_id,)).fetchall()
    conn.close()
    return {"status": "success", "posts": [dict(r) for r in rows]}


@app.get("/api/social/messages/conversations/{user_id}")
def get_message_conversations(user_id: str):
    conn = get_db()
    rows = conn.execute("""SELECT m.* FROM direct_messages m
        JOIN (SELECT MAX(id) AS latest_id FROM direct_messages WHERE sender_id=? OR recipient_id=? GROUP BY CASE WHEN sender_id=? THEN recipient_id ELSE sender_id END) x ON x.latest_id=m.id
        ORDER BY m.id DESC LIMIT 100""", (user_id, user_id, user_id)).fetchall()
    conversations = []
    seen = set()
    for row in rows:
        other_id = row["recipient_id"] if row["sender_id"] == user_id else row["sender_id"]
        if other_id in seen: continue
        seen.add(other_id)
        profile = _profile_payload(conn, other_id, user_id)
        conversations.append({"user_id": other_id, "username": profile["username"] if profile else row["sender_name"], "avatar_url": profile["avatar_url"] if profile else "", "last_message": row["body"] or ("📷 Photo" if row["media_type"].startswith("image/") else "🎥 Video"), "last_media_url": row["media_url"], "created_at": row["created_at"], "unread": 0})
    conn.close()
    return {"status": "success", "conversations": conversations}


@app.get("/api/social/messages/{user_id}/{other_id}")
def get_direct_messages(user_id: str, other_id: str, after_id: int = 0):
    conn = get_db()
    rows = conn.execute("""SELECT * FROM direct_messages WHERE ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)) AND id>? ORDER BY id ASC LIMIT 300""", (user_id, other_id, other_id, user_id, after_id)).fetchall()
    conn.execute("UPDATE direct_messages SET read_at=? WHERE sender_id=? AND recipient_id=? AND read_at=''", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), other_id, user_id))
    conn.commit()
    conn.close()
    return {"status": "success", "messages": [dict(r) for r in rows]}


@app.post("/api/social/messages")
def send_direct_message(data: DirectMessageCreate):
    sender_id = data.sender_id.strip()[:100]
    recipient_id = data.recipient_id.strip()[:100]
    body = data.body.strip()[:4000]
    media_url = data.media_url.strip()[:500]
    media_type = data.media_type.strip()[:80]
    if not sender_id or not recipient_id or sender_id == recipient_id:
        raise HTTPException(status_code=400, detail="Message recipient invalid")
    if not body and not media_url:
        raise HTTPException(status_code=400, detail="Message text ya photo/video bhejo")
    if not conn_profile_exists(recipient_id):
        raise HTTPException(status_code=404, detail="Recipient profile nahi mila")
    conn = get_db()
    _ensure_social_profile(conn, sender_id, data.sender_name)
    cursor = conn.execute("INSERT INTO direct_messages (sender_id, sender_name, recipient_id, body, media_url, media_type, created_at, read_at) VALUES (?, ?, ?, ?, ?, ?, ?, '')", (sender_id, data.sender_name.strip()[:30] or "Elvish Fan", recipient_id, body, media_url, media_type, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    row = conn.execute("SELECT * FROM direct_messages WHERE id=?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return {"status": "success", "message": dict(row)}


def conn_profile_exists(user_id: str) -> bool:
    conn = get_db()
    exists = bool(conn.execute("SELECT id FROM social_profiles WHERE user_id=?", (user_id,)).fetchone())
    conn.close()
    return exists


@app.post("/api/social/report")
def report_social_content(data: SocialReportCreate):
    reason = data.reason.strip()[:500]
    target_type = data.target_type.strip()[:30]
    if not data.reporter_id.strip() or not data.target_id.strip() or not reason:
        raise HTTPException(status_code=400, detail="Report details incomplete")
    conn = get_db()
    conn.execute("INSERT INTO social_reports (reporter_id, target_type, target_id, reason, created_at) VALUES (?, ?, ?, ?, ?)", (data.reporter_id.strip()[:100], target_type, data.target_id.strip()[:100], reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Report received. Thank you."}



# =========================================================
# ELVISH ARMY VIP PREMIUM — RAZORPAY SUBSCRIPTIONS
# Add this section at the END of main.py
# =========================================================

import hashlib
import hmac
import urllib.request
import urllib.error
from datetime import datetime, timezone


def premium_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS premium_subscriptions (
            user_id TEXT PRIMARY KEY,
            subscription_id TEXT UNIQUE,
            status TEXT NOT NULL DEFAULT 'created',
            payment_id TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def premium_now():
    return datetime.now(timezone.utc).isoformat()


def razorpay_setting(name):
    return os.getenv(name, "").strip()


def razorpay_api(method, endpoint, payload=None):
    key_id = razorpay_setting("RAZORPAY_KEY_ID")
    key_secret = razorpay_setting("RAZORPAY_KEY_SECRET")

    if not key_id or not key_secret:
        raise HTTPException(
            status_code=503,
            detail="Razorpay keys Render Environment mein configure nahi hain."
        )

    raw = json.dumps(payload or {}).encode("utf-8")
    auth = base64.b64encode(
        f"{key_id}:{key_secret}".encode("utf-8")
    ).decode("ascii")

    request = urllib.request.Request(
        "https://api.razorpay.com/v1/" + endpoint.lstrip("/"),
        data=raw if method.upper() != "GET" else None,
        method=method.upper(),
        headers={
            "Authorization": "Basic " + auth,
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.error("Razorpay API error: %s", detail[:1000])
        raise HTTPException(
            status_code=502,
            detail="Razorpay request fail hui. Plan ID aur API settings check karein."
        )
    except Exception as exc:
        logger.exception("Razorpay connection error")
        raise HTTPException(
            status_code=502,
            detail="Razorpay se connect nahi ho paya. Thodi der baad try karein."
        )


@app.post("/api/premium/create-subscription")
async def premium_create_subscription(request: Request):
    body = await request.json()
    user_id = str(body.get("user_id", "")).strip()

    if not user_id or len(user_id) > 200:
        raise HTTPException(status_code=400, detail="Valid user_id required.")

    plan_id = razorpay_setting("RAZORPAY_PLAN_ID")
    if not plan_id:
        raise HTTPException(
            status_code=503,
            detail="RAZORPAY_PLAN_ID Render Environment mein set karein."
        )

    conn = premium_db()
    try:
        existing = conn.execute(
            "SELECT subscription_id, status FROM premium_subscriptions WHERE user_id=?",
            (user_id,)
        ).fetchone()

        if existing and existing["status"] == "active":
            return {
                "is_premium": True,
                "status": "active"
            }

        # Reuse an existing subscription that is still in checkout/created state.
        if existing and existing["subscription_id"] and existing["status"] == "created":
            return {
                "subscription_id": existing["subscription_id"],
                "key_id": razorpay_setting("RAZORPAY_KEY_ID"),
                "status": "created"
            }

        subscription = razorpay_api("POST", "/subscriptions", {
            "plan_id": plan_id,
            "total_count": 120,
            "quantity": 1,
            "customer_notify": 1,
            "notes": {
                "user_id": user_id,
                "app": "elvish_army"
            }
        })

        subscription_id = subscription.get("id")
        if not subscription_id:
            raise HTTPException(
                status_code=502,
                detail="Razorpay ne subscription ID return nahi ki."
            )

        conn.execute("""
            INSERT INTO premium_subscriptions
                (user_id, subscription_id, status, payment_id, updated_at)
            VALUES (?, ?, 'created', NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                subscription_id=excluded.subscription_id,
                status='created',
                payment_id=NULL,
                updated_at=excluded.updated_at
        """, (user_id, subscription_id, premium_now()))
        conn.commit()

        return {
            "subscription_id": subscription_id,
            "key_id": razorpay_setting("RAZORPAY_KEY_ID"),
            "status": "created"
        }
    finally:
        conn.close()


@app.post("/api/premium/verify")
async def premium_verify_payment(request: Request):
    body = await request.json()

    user_id = str(body.get("user_id", "")).strip()
    payment_id = str(body.get("razorpay_payment_id", "")).strip()
    subscription_id = str(body.get("razorpay_subscription_id", "")).strip()
    signature = str(body.get("razorpay_signature", "")).strip()
    secret = razorpay_setting("RAZORPAY_KEY_SECRET")

    if not all([user_id, payment_id, subscription_id, signature, secret]):
        raise HTTPException(status_code=400, detail="Payment verification data incomplete.")

    conn = premium_db()
    try:
        row = conn.execute("""
            SELECT user_id FROM premium_subscriptions
            WHERE user_id=? AND subscription_id=?
        """, (user_id, subscription_id)).fetchone()

        if not row:
            raise HTTPException(status_code=400, detail="Subscription record match nahi hua.")

        signed_data = f"{subscription_id}|{payment_id}".encode("utf-8")
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_data,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Payment signature invalid.")

        # Razorpay checkout signature verified. Webhook events continue
        # to synchronize later renewals/cancellations.
        conn.execute("""
            UPDATE premium_subscriptions
            SET status='active', payment_id=?, updated_at=?
            WHERE user_id=? AND subscription_id=?
        """, (payment_id, premium_now(), user_id, subscription_id))
        conn.commit()

        return {"ok": True, "is_premium": True, "status": "active"}
    finally:
        conn.close()


@app.get("/api/premium/status")
async def premium_status(user_id: str):
    user_id = user_id.strip()
    if not user_id or len(user_id) > 200:
        raise HTTPException(status_code=400, detail="Valid user_id required.")

    conn = premium_db()
    try:
        row = conn.execute("""
            SELECT status, updated_at FROM premium_subscriptions WHERE user_id=?
        """, (user_id,)).fetchone()

        status = row["status"] if row else "inactive"
        return {
            "is_premium": status == "active",
            "status": status,
            "updated_at": row["updated_at"] if row else None
        }
    finally:
        conn.close()


@app.post("/api/premium/webhook")
async def premium_razorpay_webhook(request: Request):
    webhook_secret = razorpay_setting("RAZORPAY_WEBHOOK_SECRET")
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not webhook_secret or not signature:
        raise HTTPException(status_code=400, detail="Webhook signature missing.")

    raw_body = await request.body()
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    try:
        event_data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON.")

    event = event_data.get("event", "")
    entity = (
        event_data.get("payload", {})
        .get("subscription", {})
        .get("entity", {})
    )
    subscription_id = str(entity.get("id", "")).strip()
    notes = entity.get("notes") or {}
    user_id = str(notes.get("user_id", "")).strip()

    status_map = {
        "subscription.activated": "active",
        "subscription.charged": "active",
        "subscription.authenticated": "active",
        "subscription.cancelled": "cancelled",
        "subscription.halted": "halted",
        "subscription.completed": "completed",
        "subscription.expired": "expired"
    }

    new_status = status_map.get(event)
    if not new_status or not subscription_id:
        return {"ok": True, "ignored": True}

    conn = premium_db()
    try:
        # Prefer subscription ID lookup; use notes user ID only as a fallback.
        row = conn.execute(
            "SELECT user_id FROM premium_subscriptions WHERE subscription_id=?",
            (subscription_id,)
        ).fetchone()

        if row:
            user_id = row["user_id"]

        if user_id:
            conn.execute("""
                UPDATE premium_subscriptions
                SET status=?, updated_at=?
                WHERE user_id=? AND subscription_id=?
            """, (new_status, premium_now(), user_id, subscription_id))
            conn.commit()
    finally:
        conn.close()

    return {"ok": True}