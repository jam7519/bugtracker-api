from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from typing import Optional
from fastapi import Header 
from sqlalchemy import create_engine, text
import os

# Load variables from .env
load_dotenv()

# Read the database connection string
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing. Check your .env file.")

# Create a reusable connection factory
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

app = FastAPI(title="BugTracker API")


# -----------------------
# Basic endpoints
# -----------------------
@app.get("/")
def root():
    return {"message": "BugTracker API is running", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------
# Pydantic schemas (input validation)
# -----------------------
class BugCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=3, max_length=5000)
    priority: str = Field(..., pattern="^(low|medium|high|critical)$")
    status: str = Field("open", pattern="^(open|in_progress|closed)$")


class BugUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = Field(None, min_length=3, max_length=5000)
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$")
    status: Optional[str] = Field(None, pattern="^(open|in_progress|closed)$")



class CommentCreate(BaseModel):
    author: str = Field(..., min_length=1, max_length=100)
    comment: str = Field(..., min_length=1, max_length=2000)


# -----------------------
# Bugs (PostgreSQL-backed)
# -----------------------
@app.get("/bugs")
def list_bugs():
    sql = """
        SELECT bug_id, title, description, priority, status, created_at, updated_at, resolved_at
        FROM bugs
        ORDER BY bug_id DESC;
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return list(rows)


@app.post("/bugs", status_code=201)
def create_bug(payload: BugCreate):
    sql = """
        INSERT INTO bugs (title, description, priority, status, created_at, updated_at, resolved_at)
        VALUES (:title, :description, :priority, :status, NOW(), NOW(),
                CASE WHEN :status = 'closed' THEN NOW() ELSE NULL END)
        RETURNING bug_id, title, description, priority, status, created_at, updated_at, resolved_at;
    """
    with engine.begin() as conn:
        row = conn.execute(text(sql), payload.model_dump()).mappings().one()
    return dict(row)


@app.get("/bugs/{bug_id}")
def get_bug(bug_id: int):
    sql = """
        SELECT bug_id, title, description, priority, status, created_at, updated_at, resolved_at
        FROM bugs
        WHERE bug_id = :bug_id;
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"bug_id": bug_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Bug not found")

    return dict(row)

@app.patch("/bugs/{bug_id}")
def update_bug(bug_id: int, payload: BugUpdate):
    # Build dynamic SET clause (only update provided fields)
    fields = []
    values = {"bug_id": bug_id}

    for key, value in payload.model_dump(exclude_none=True).items():
        fields.append(f"{key} = :{key}")
        values[key] = value

    if not fields:
        raise HTTPException(status_code=400, detail="No fields provided for update")

    # Auto-handle resolved_at when status changes
    if "status" in values:
        fields.append(
            "resolved_at = CASE WHEN :status = 'closed' THEN NOW() ELSE NULL END"
        )

    sql = f"""
    UPDATE bugs
    SET {", ".join(fields + ["updated_at = NOW()"])}
    WHERE bug_id = :bug_id
    RETURNING bug_id, title, description, priority, status,
              created_at, updated_at, resolved_at;
"""


    with engine.begin() as conn:
        row = conn.execute(text(sql), values).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Bug not found")

    return dict(row)

# -----------------------
# Comments (PostgreSQL-backed)
# -----------------------
@app.post("/bugs/{bug_id}/comments", status_code=201)
def add_comment(bug_id: int, payload: CommentCreate):
    # Ensure bug exists
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM bugs WHERE bug_id = :bug_id"),
            {"bug_id": bug_id}
        ).fetchone()

    if not exists:
        raise HTTPException(status_code=404, detail="Bug not found")

    insert_sql = """
        INSERT INTO bug_comments (bug_id, author, comment)
        VALUES (:bug_id, :author, :comment)
        RETURNING comment_id, bug_id, author, comment, created_at;
    """

    with engine.begin() as conn:
        row = conn.execute(
            text(insert_sql),
            {"bug_id": bug_id, "author": payload.author, "comment": payload.comment}
        ).mappings().one()

    return dict(row)


@app.get("/bugs/{bug_id}/comments")
def list_comments(bug_id: int):
    sql = """
        SELECT comment_id, bug_id, author, comment, created_at
        FROM bug_comments
        WHERE bug_id = :bug_id
        ORDER BY created_at ASC;
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"bug_id": bug_id}).mappings().all()
    return list(rows)
