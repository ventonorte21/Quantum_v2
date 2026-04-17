from fastapi import APIRouter, HTTPException, Response, Request
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone, timedelta
import httpx
import uuid
import os
import logging
import secrets

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_CONCURRENT_SESSIONS = 5


async def _upsert_session(user_id: str, session_token: str, expires_at: datetime) -> None:
    """Insert a new session, cleaning up only expired ones first.

    We intentionally do NOT wipe all active sessions on each login so that
    multiple browser tabs / Replit proxy connections can stay authenticated
    simultaneously without invalidating each other.
    """
    now = datetime.now(timezone.utc)
    # 1. Remove expired sessions for this user only
    await _db.user_sessions.delete_many({"user_id": user_id, "expires_at": {"$lt": now}})
    # 2. If still over the cap, evict the oldest active session(s)
    active = await _db.user_sessions.find(
        {"user_id": user_id}, {"_id": 1, "created_at": 1}
    ).sort("created_at", 1).to_list(None)
    if len(active) >= MAX_CONCURRENT_SESSIONS:
        to_evict = [s["_id"] for s in active[: len(active) - MAX_CONCURRENT_SESSIONS + 1]]
        await _db.user_sessions.delete_many({"_id": {"$in": to_evict}})
    # 3. Insert the new session
    await _db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": expires_at,
        "created_at": now,
    })

# Whitelist of allowed emails — only these can access the trading terminal
ALLOWED_EMAILS = [
    "bruno.caiado@gmail.com",
]

# Database reference (set during startup)
_db = None

def set_auth_db(db):
    global _db
    _db = db

EMERGENTAGENT_SESSION_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"


@auth_router.get("/session/exchange")
async def exchange_session_get(session_id: str, response: Response):
    """GET companion for /auth/session — proxy-safe (no JSON body)."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    logger.info(f"Session exchange (GET): calling emergentagent with session_id prefix={session_id[:8]}...")
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(EMERGENTAGENT_SESSION_URL, headers={"X-Session-ID": session_id})
        except httpx.RequestError as e:
            logger.error(f"Auth session exchange network error: {e}")
            raise HTTPException(status_code=502, detail="Authentication service unavailable")
    logger.info(f"Emergentagent response: status={resp.status_code} body_preview={resp.text[:200]}")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session_id")
    data = resp.json()
    email = data.get("email", "").lower().strip()
    name = data.get("name", "")
    picture = data.get("picture", "")
    session_token = data.get("session_token", "")
    logger.info(f"Session exchange: email={email} has_token={bool(session_token)}")
    if not email or not session_token:
        raise HTTPException(status_code=401, detail="Invalid session data")
    if email not in [e.lower() for e in ALLOWED_EMAILS]:
        logger.warning(f"Auth DENIED: {email} not in whitelist {ALLOWED_EMAILS}")
        raise HTTPException(status_code=403, detail=f"Acesso negado. O email {email} nao esta autorizado.")
    # Upsert atômico: evita race condition entre find_one + insert_one concorrentes
    # (ex: duplo clique ou dois requests de login simultâneos para o mesmo email).
    _now = datetime.now(timezone.utc)
    user_doc = await _db.users.find_one_and_update(
        {"email": email},
        {
            "$set": {"name": name, "picture": picture, "last_login": _now},
            "$setOnInsert": {
                "user_id": f"user_{uuid.uuid4().hex[:12]}",
                "created_at": _now,
            },
        },
        upsert=True,
        return_document=True,
    )
    user_id = user_doc["user_id"]
    expires_at = _now + timedelta(days=7)
    await _upsert_session(user_id, session_token, expires_at)
    response.set_cookie(key="session_token", value=session_token, httponly=True,
                        secure=True, samesite="none", path="/", max_age=7 * 24 * 3600)
    logger.info(f"Session exchange (GET) for {email}")
    return {"user_id": user_id, "email": email, "name": name, "picture": picture, "session_token": session_token}


@auth_router.post("/session")
async def exchange_session(request: Request, response: Response):
    """Exchange Emergent OAuth session_id for a persistent session_token."""
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    # Call Emergent Auth to validate session
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                EMERGENTAGENT_SESSION_URL,
                headers={"X-Session-ID": session_id},
            )
        except httpx.RequestError as e:
            logger.error(f"Auth session exchange failed: {e}")
            raise HTTPException(status_code=502, detail="Authentication service unavailable")

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session_id")

    data = resp.json()
    email = data.get("email", "").lower().strip()
    name = data.get("name", "")
    picture = data.get("picture", "")
    session_token = data.get("session_token", "")

    if not email or not session_token:
        raise HTTPException(status_code=401, detail="Invalid session data")

    # Whitelist check
    if email not in [e.lower() for e in ALLOWED_EMAILS]:
        logger.warning(f"Auth DENIED: {email} not in whitelist")
        raise HTTPException(
            status_code=403,
            detail=f"Acesso negado. O email {email} nao esta autorizado."
        )

    # Upsert atômico: evita race condition entre find_one + insert_one concorrentes.
    _now = datetime.now(timezone.utc)
    user_doc = await _db.users.find_one_and_update(
        {"email": email},
        {
            "$set": {"name": name, "picture": picture, "last_login": _now},
            "$setOnInsert": {
                "user_id": f"user_{uuid.uuid4().hex[:12]}",
                "created_at": _now,
            },
        },
        upsert=True,
        return_document=True,
    )
    user_id = user_doc["user_id"]

    # Store session
    expires_at = _now + timedelta(days=7)
    await _upsert_session(user_id, session_token, expires_at)

    # Set httpOnly cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=7 * 24 * 3600,
    )

    return {
        "user_id": user_id,
        "email": email,
        "name": name,
        "picture": picture,
        "session_token": session_token,
    }


@auth_router.get("/passcode/login")
async def passcode_login_get(passcode: str, response: Response):
    """GET companion for /auth/passcode — proxy-safe (no JSON body)."""
    passcode = passcode.strip()
    if not passcode:
        raise HTTPException(status_code=400, detail="Código de acesso obrigatório")
    configured = os.environ.get("DASHBOARD_PASSCODE", "").strip()
    dev_fallback = os.environ.get("DEV_PASSCODE", "").strip()
    if not configured and not dev_fallback:
        logger.error("Passcode login: neither DASHBOARD_PASSCODE nor DEV_PASSCODE is configured")
        raise HTTPException(status_code=503, detail="Login por código não configurado")
    match = False
    if configured:
        match = secrets.compare_digest(passcode, configured)
    if not match and dev_fallback:
        match = secrets.compare_digest(passcode, dev_fallback)
    logger.info(f"Passcode login attempt: dashboard_set={bool(configured)} dev_set={bool(dev_fallback)} match={match}")
    if not match:
        raise HTTPException(status_code=401, detail="Código de acesso inválido")
    email = ALLOWED_EMAILS[0]
    session_token = secrets.token_urlsafe(32)
    user_id = None
    existing = await _db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await _db.users.update_one({"email": email}, {"$set": {"last_login": datetime.now(timezone.utc)}})
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await _db.users.insert_one({
            "user_id": user_id, "email": email, "name": "Bruno Caiado",
            "picture": "", "created_at": datetime.now(timezone.utc), "last_login": datetime.now(timezone.utc),
        })
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await _upsert_session(user_id, session_token, expires_at)
    response.set_cookie(key="session_token", value=session_token, httponly=True,
                        secure=True, samesite="none", path="/", max_age=7 * 24 * 3600)
    logger.info(f"Passcode login (GET) successful for {email}")
    return {"user_id": user_id, "email": email, "name": "Bruno Caiado", "picture": "", "session_token": session_token}


@auth_router.post("/passcode")
async def passcode_login(request: Request, response: Response):
    """Login with a passcode (for environments where Google OAuth redirect fails)."""
    body = await request.json()
    passcode = body.get("passcode", "").strip()

    if not passcode:
        raise HTTPException(status_code=400, detail="Código de acesso obrigatório")

    # Read configured passcode from environment
    configured = os.environ.get("DASHBOARD_PASSCODE", "").strip()
    dev_fallback = os.environ.get("DEV_PASSCODE", "").strip()
    if not configured and not dev_fallback:
        logger.error("Passcode login: neither DASHBOARD_PASSCODE nor DEV_PASSCODE is configured")
        raise HTTPException(status_code=503, detail="Login por código não configurado")

    # Allow match against configured secret OR the DEV_PASSCODE fallback
    match = False
    if configured:
        match = secrets.compare_digest(passcode, configured)
    if not match and dev_fallback:
        match = secrets.compare_digest(passcode, dev_fallback)
    logger.info(f"Passcode login attempt (POST): dashboard_set={bool(configured)} dev_set={bool(dev_fallback)} match={match}")
    if not match:
        raise HTTPException(status_code=401, detail="Código de acesso inválido")

    # Create a session for the whitelisted user
    email = ALLOWED_EMAILS[0]
    session_token = secrets.token_urlsafe(32)
    user_id = None

    existing = await _db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await _db.users.update_one(
            {"email": email},
            {"$set": {"last_login": datetime.now(timezone.utc)}}
        )
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await _db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": "Bruno Caiado",
            "picture": "",
            "created_at": datetime.now(timezone.utc),
            "last_login": datetime.now(timezone.utc),
        })

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await _upsert_session(user_id, session_token, expires_at)

    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=7 * 24 * 3600,
    )

    logger.info(f"Passcode login successful for {email}")
    return {
        "user_id": user_id,
        "email": email,
        "name": "Bruno Caiado",
        "picture": "",
        "session_token": session_token,
    }


@auth_router.post("/dev-login")
async def dev_login(response: Response):
    """Direct login for development environment — no password required."""
    if os.environ.get("DEV_MODE", "").lower() != "true":
        raise HTTPException(status_code=403, detail="Dev login not available")

    email = ALLOWED_EMAILS[0]
    session_token = secrets.token_urlsafe(32)

    existing = await _db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await _db.users.update_one({"email": email}, {"$set": {"last_login": datetime.now(timezone.utc)}})
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await _db.users.insert_one({
            "user_id": user_id, "email": email, "name": "Bruno Caiado",
            "picture": "", "created_at": datetime.now(timezone.utc), "last_login": datetime.now(timezone.utc),
        })

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await _upsert_session(user_id, session_token, expires_at)

    response.set_cookie(key="session_token", value=session_token, httponly=True,
                        secure=True, samesite="none", path="/", max_age=7 * 24 * 3600)

    logger.info(f"Dev login for {email}")
    return {"user_id": user_id, "email": email, "name": "Bruno Caiado",
            "picture": "", "session_token": session_token}


async def _get_current_user(request: Request):
    """Helper: validate session from cookie or Authorization header."""
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None

    session_doc = await _db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session_doc:
        return None

    # Check expiry
    expires_at = session_doc["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        await _db.user_sessions.delete_one({"session_token": token})
        return None

    user_doc = await _db.users.find_one({"user_id": session_doc["user_id"]}, {"_id": 0})
    return user_doc


@auth_router.get("/me")
async def get_current_user(request: Request):
    """Return current authenticated user or 401."""
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user["name"],
        "picture": user.get("picture", ""),
    }


@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    """Clear session from DB and cookie."""
    token = request.cookies.get("session_token")
    if token:
        await _db.user_sessions.delete_many({"session_token": token})
    response.delete_cookie(
        key="session_token",
        path="/",
        secure=True,
        samesite="none",
    )
    return {"status": "logged_out"}
