"""
SwingIQ — Authentication
JWT-based auth: register, login, token verification.
Uses bcrypt for password hashing.
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, get_user_by_email, get_user_by_username, create_user, User

# ─── CONFIG ──────────────────────────────────────────────────────────────────
SECRET_KEY      = os.getenv("JWT_SECRET", "swingiq-dev-secret-change-in-production")
ALGORITHM       = "HS256"
TOKEN_EXPIRE_H  = int(os.getenv("TOKEN_EXPIRE_HOURS", "72"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer      = HTTPBearer(auto_error=False)


# ─── SCHEMAS ─────────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email:    str
    username: str
    password: str
    handicap: Optional[float] = None


class LoginRequest(BaseModel):
    email:    str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      int
    username:     str


class UserOut(BaseModel):
    id:         int
    email:      str
    username:   str
    handicap:   Optional[float]
    created_at: datetime

    class Config:
        from_attributes = True


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, username: str) -> str:
    expire  = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_H)
    payload = {"sub": str(user_id), "username": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token ungültig oder abgelaufen.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─── DEPENDENCY ──────────────────────────────────────────────────────────────
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt.")
    payload = decode_token(credentials.credentials)
    user    = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Benutzer nicht gefunden.")
    return user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Like get_current_user but returns None instead of raising."""
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        return db.query(User).filter(User.id == int(payload["sub"])).first()
    except HTTPException:
        return None


# ─── ROUTE HANDLERS ──────────────────────────────────────────────────────────
async def register_handler(req: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    if get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert.")
    if get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Benutzername bereits vergeben.")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 6 Zeichen haben.")

    user  = create_user(db, req.email, req.username, hash_password(req.password))
    token = create_access_token(user.id, user.username)
    return TokenResponse(access_token=token, user_id=user.id, username=user.username)


async def login_handler(req: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = get_user_by_email(db, req.email)
    if not user or not verify_password(req.password, user.hashed_pw):
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch.")
    token = create_access_token(user.id, user.username)
    return TokenResponse(access_token=token, user_id=user.id, username=user.username)
