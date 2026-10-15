import os
import secrets
from typing import Optional
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException, status, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.db_models import User

# Session secret configuration
SECRET_KEY = os.environ.get("SESSION_SECRET_KEY") or os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    # Stable fallback key for dev or generated
    SECRET_KEY = "news-agent-session-secret-key-change-in-production-12345"

SESSION_COOKIE_NAME = "session_id"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

serializer = URLSafeTimedSerializer(SECRET_KEY, salt="auth-session-salt")

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False

def create_session_token(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id})

def decode_session_token(token: str) -> Optional[int]:
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("user_id")
    except (BadSignature, SignatureExpired, Exception):
        return None

async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    user_id = decode_session_token(token)
    if not user_id:
        return None
    
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    return user

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    user = await get_current_user_optional(request, db)
    if not user:
        accept = request.headers.get("accept", "")
        path = request.url.path
        is_html_page = (
            "text/html" in accept 
            or path in ["/", "/story", "/saved", "/setting", "/crawl", "/filter"]
        )
        if is_html_page and not path.startswith("/api/"):
            redirect_target = str(request.url.path)
            if request.url.query:
                redirect_target += f"?{request.url.query}"
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": f"/login?next={redirect_target}"}
            )
        # API / JSON request
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required."
        )
    return user

async def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required."
        )
    return current_user
