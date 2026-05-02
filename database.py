"""
SwingIQ — Database Layer
SQLAlchemy models + session management.
Default: SQLite (file-based, zero config)
Switch to PostgreSQL: set DATABASE_URL=postgresql://... in .env
"""

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    Text, DateTime, ForeignKey, Boolean, JSON
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# ─── ENGINE ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./swingiq.db")

# SQLite needs check_same_thread=False; ignored for other DBs
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ─── MODELS ──────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String(255), unique=True, index=True, nullable=False)
    username   = Column(String(100), unique=True, index=True, nullable=False)
    hashed_pw  = Column(String(255), nullable=False)
    handicap   = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active  = Column(Boolean, default=True)

    sessions     = relationship("SwingSession", back_populates="user", cascade="all, delete-orphan")
    chat_history = relationship("ChatMessage",  back_populates="user", cascade="all, delete-orphan")


class SwingSession(Base):
    """One recorded swing analysis session."""
    __tablename__ = "swing_sessions"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    label       = Column(String(200), default="Schwunganalyse")
    club_type   = Column(String(50), default="Driver")   # Driver, Iron, Putt…
    score       = Column(Integer, nullable=True)          # 0–100 overall
    created_at  = Column(DateTime, default=datetime.utcnow)
    notes       = Column(Text, nullable=True)

    user    = relationship("User", back_populates="sessions")
    metrics = relationship("SwingMetric", back_populates="session", cascade="all, delete-orphan")
    chats   = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class SwingMetric(Base):
    """Individual biomechanical measurement within a session."""
    __tablename__ = "swing_metrics"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("swing_sessions.id"), nullable=False, index=True)
    name       = Column(String(100), nullable=False)   # e.g. "hueftrotation"
    value      = Column(Float, nullable=True)
    unit       = Column(String(20), default="")        # °, cm, mph, …
    ideal_min  = Column(Float, nullable=True)
    ideal_max  = Column(Float, nullable=True)
    status     = Column(String(20), default="unknown") # good / warn / bad

    session = relationship("SwingSession", back_populates="metrics")


class ChatMessage(Base):
    """Persisted coaching chat messages per user (and optionally per session)."""
    __tablename__ = "chat_messages"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("swing_sessions.id"), nullable=True)
    role       = Column(String(20), nullable=False)   # "user" | "assistant"
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user    = relationship("User",         back_populates="chat_history")
    session = relationship("SwingSession", back_populates="chats")


# ─── INIT ────────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables (safe to call multiple times)."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── CRUD HELPERS ────────────────────────────────────────────────────────────

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, email: str, username: str, hashed_pw: str) -> User:
    user = User(email=email, username=username, hashed_pw=hashed_pw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_session(
    db: Session, user_id: int, label: str, club_type: str,
    score: int, notes: str, metrics: list[dict]
) -> SwingSession:
    s = SwingSession(
        user_id=user_id, label=label, club_type=club_type,
        score=score, notes=notes,
    )
    db.add(s)
    db.flush()   # get s.id without committing

    for m in metrics:
        db.add(SwingMetric(
            session_id=s.id,
            name=m["name"],
            value=m.get("value"),
            unit=m.get("unit", ""),
            ideal_min=m.get("ideal_min"),
            ideal_max=m.get("ideal_max"),
            status=m.get("status", "unknown"),
        ))

    db.commit()
    db.refresh(s)
    return s


def get_user_sessions(db: Session, user_id: int, limit: int = 50) -> list[SwingSession]:
    return (
        db.query(SwingSession)
        .filter(SwingSession.user_id == user_id)
        .order_by(SwingSession.created_at.desc())
        .limit(limit)
        .all()
    )


def save_chat_message(
    db: Session, user_id: int, role: str, content: str,
    session_id: Optional[int] = None
) -> ChatMessage:
    msg = ChatMessage(user_id=user_id, role=role, content=content, session_id=session_id)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_chat_history(
    db: Session, user_id: int, session_id: Optional[int] = None, limit: int = 40
) -> list[ChatMessage]:
    q = db.query(ChatMessage).filter(ChatMessage.user_id == user_id)
    if session_id:
        q = q.filter(ChatMessage.session_id == session_id)
    return q.order_by(ChatMessage.created_at.asc()).limit(limit).all()
