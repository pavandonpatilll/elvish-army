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

DB_NAME = "elvish_army.db"


# =====================================================
# ADMIN
# =====================================================

ADMIN_USERNAME = "admin"

ADMIN_PASSWORD = "elvish123"


# =====================================================
# ADMIN TOKENS
# =====================================================

admin_tokens = set()

# =====================================================
# DATABASE
# =====================================================

def get_db():

    conn = sqlite3.connect(DB_NAME)

    conn.row_factory = sqlite3.Row

    return conn


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

    