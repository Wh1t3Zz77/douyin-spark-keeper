from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import secrets
import threading

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, inspect, or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .mailer import enqueue_email
from .models import (
    DouyinAccount,
    BindingSession,
    Contact,
    ContactSync,
    InviteCode,
    LoginSession,
    NotificationPreference,
    PasswordReset,
    PendingRegistration,
    Run,
    RunItem,
    Task,
    TaskRecipient,
    User,
    UserEntitlement,
    utcnow,
)
from .schemas import AccountCreate, BindingVerification, ContactSelectionInput, ContactTypeInput, InviteCreate, InviteStatusInput, LoginInput, NotificationInput, PasswordChange, PasswordResetStart, PasswordResetVerify, ProUpgradeInput, RegisterStart, RegisterVerify, TaskCreate, TaskStatusInput
from .security import digest, encrypt_text, expires_in, hash_password, normalize_email, random_code, random_token, verify_password


settings = get_settings()
FREE_TRIAL_DAYS = 7
FREE_TASK_LIMIT = 5
FREE_RECIPIENT_LIMIT = 10
RECIPIENTS_PER_TASK = 5
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_FAILURE_LIMIT = 8
_login_failures: dict[str, list[datetime]] = {}
_login_failures_lock = threading.Lock()


def _validate_configuration() -> None:
    if len(settings.app_secret) < 32 or "CHANGE_ME" in settings.app_secret.upper():
        raise RuntimeError("APP_SECRET 必须替换为至少 32 位的随机字符串")
    if settings.bootstrap_invite and "CHANGE_ME" in settings.bootstrap_invite.upper():
        raise RuntimeError("BOOTSTRAP_INVITE 仍是示例值，请替换为私有邀请码")
    if settings.email_verification_required and (not settings.smtp_host or not settings.smtp_from):
        raise RuntimeError("启用邮箱验证时必须配置 SMTP_HOST 和 SMTP_FROM")
    if settings.app_env == "production" and not settings.session_secure:
        raise RuntimeError("公网生产环境必须设置 SESSION_SECURE=true")


def _seed_invite(db: Session) -> None:
    if not settings.bootstrap_invite:
        return
    code_digest = digest(settings.bootstrap_invite.strip().upper())
    if not db.scalar(select(InviteCode).where(InviteCode.code_digest == code_digest)):
        db.add(InviteCode(code_digest=code_digest, max_uses=settings.bootstrap_invite_uses))
        db.commit()


def _migrate_schema() -> None:
    inspector = inspect(engine)
    if "tasks" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("tasks")}
        if "deleted_at" not in columns:
            with engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN deleted_at DATETIME")
                connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_tasks_deleted_at ON tasks (deleted_at)")
    if "user_entitlements" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("user_entitlements")}
        with engine.begin() as connection:
            if "expires_at" not in columns:
                connection.exec_driver_sql("ALTER TABLE user_entitlements ADD COLUMN expires_at DATETIME")
            if "plan_period" not in columns:
                connection.exec_driver_sql("ALTER TABLE user_entitlements ADD COLUMN plan_period VARCHAR(16)")
    if "binding_sessions" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("binding_sessions")}
        with engine.begin() as connection:
            if "verification_code_ciphertext" not in columns:
                connection.exec_driver_sql("ALTER TABLE binding_sessions ADD COLUMN verification_code_ciphertext TEXT")
            if "verification_attempts" not in columns:
                connection.exec_driver_sql("ALTER TABLE binding_sessions ADD COLUMN verification_attempts INTEGER DEFAULT 0 NOT NULL")
    if "task_recipients" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("task_recipients")}
        with engine.begin() as connection:
            if "contact_id" not in columns:
                connection.exec_driver_sql("ALTER TABLE task_recipients ADD COLUMN contact_id VARCHAR(36)")
            if "conversation_type" not in columns:
                connection.exec_driver_sql("ALTER TABLE task_recipients ADD COLUMN conversation_type VARCHAR(16) DEFAULT 'unknown' NOT NULL")
    if "contacts" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("contacts")}
        if "type_locked" not in columns:
            with engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE contacts ADD COLUMN type_locked BOOLEAN DEFAULT 0 NOT NULL")
    if "runs" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("runs")}
        if "parent_run_id" not in columns:
            with engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE runs ADD COLUMN parent_run_id VARCHAR(36)")
                connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_runs_parent_run_id ON runs (parent_run_id)")
    if "run_items" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("run_items")}
        with engine.begin() as connection:
            if "started_at" not in columns:
                connection.exec_driver_sql("ALTER TABLE run_items ADD COLUMN started_at DATETIME")
            if "finished_at" not in columns:
                connection.exec_driver_sql("ALTER TABLE run_items ADD COLUMN finished_at DATETIME")
    if "email_outbox" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("email_outbox")}
        if "dedup_key" not in columns:
            with engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE email_outbox ADD COLUMN dedup_key VARCHAR(160)")
                connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_email_outbox_dedup_key ON email_outbox (dedup_key)")


def _backfill_entitlements(db: Session) -> None:
    changed = False
    for user in db.scalars(select(User)).all():
        entitlement = db.get(UserEntitlement, user.id)
        free_expires_at = user.created_at + timedelta(days=FREE_TRIAL_DAYS)
        legacy_expires_at = user.created_at + timedelta(days=30)
        if not entitlement:
            db.add(UserEntitlement(user_id=user.id, plan_tier="free", expires_at=free_expires_at))
            changed = True
        elif entitlement.plan_tier == "free":
            is_legacy_trial = entitlement.expires_at and abs((entitlement.expires_at - legacy_expires_at).total_seconds()) < 60
            if not entitlement.expires_at or is_legacy_trial:
                entitlement.expires_at = free_expires_at
                changed = True
        elif entitlement.plan_tier == "pro" and not entitlement.plan_period:
            entitlement.plan_period = "year"
            changed = True
    if changed:
        db.commit()


def _repair_legacy_retrying_runs(db: Session) -> None:
    """Close pre-V36 retry rows once their later attempt has already finished."""
    now = utcnow()
    changed = False
    parents = db.scalars(select(Run).where(Run.status == "retrying", Run.parent_run_id.is_(None))).all()
    for parent in parents:
        current = parent
        while True:
            child = db.scalar(
                select(Run)
                .where(
                    Run.task_id == parent.task_id,
                    Run.attempt == current.attempt + 1,
                    Run.scheduled_for > current.scheduled_for,
                    Run.scheduled_for <= current.scheduled_for + timedelta(hours=6),
                )
                .order_by(Run.scheduled_for)
            )
            if not child or child.status != "retrying":
                break
            current = child
        if child and child.status not in {"pending", "running", "retrying"}:
            parent.status = "recovered" if child.status == "success" else child.status
            parent.error_code = child.error_code
            parent.error_message = f"第 {child.attempt} 次尝试已成功" if child.status == "success" else child.error_message
            changed = True
        elif not child and parent.finished_at and parent.finished_at < now - timedelta(hours=6):
            parent.status = "failed"
            parent.error_code = "RETRY_RECORD_INCOMPLETE"
            parent.error_message = "历史重试已经结束，但缺少可关联的后续记录"
            changed = True
    if changed:
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _validate_configuration()
    Base.metadata.create_all(engine)
    _migrate_schema()
    with SessionLocal() as db:
        _seed_invite(db)
        _backfill_entitlements(db)
        _repair_legacy_retrying_runs(db)
        now = utcnow()
        db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
        db.execute(delete(PendingRegistration).where(PendingRegistration.code_expires_at <= now - timedelta(days=1)))
        db.execute(delete(PasswordReset).where(PasswordReset.code_expires_at <= now - timedelta(days=1)))
        db.commit()
    yield


app = FastAPI(
    title="Spark Keeper API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def enforce_origin(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in settings.origins:
            return Response(status_code=403, content="Invalid origin")
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if settings.app_env == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def _login_key(request: Request, identity: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"{client}:{normalize_email(identity).lower()}"


def _login_is_limited(key: str, now: datetime) -> bool:
    with _login_failures_lock:
        recent = [value for value in _login_failures.get(key, []) if value > now - LOGIN_WINDOW]
        _login_failures[key] = recent
        return len(recent) >= LOGIN_FAILURE_LIMIT


def _record_login_failure(key: str, now: datetime) -> None:
    with _login_failures_lock:
        recent = [value for value in _login_failures.get(key, []) if value > now - LOGIN_WINDOW]
        recent.append(now)
        _login_failures[key] = recent


def _clear_login_failures(key: str) -> None:
    with _login_failures_lock:
        _login_failures.pop(key, None)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    session = db.scalar(
        select(LoginSession).where(LoginSession.token_digest == digest(token), LoginSession.expires_at > utcnow())
    )
    if not session or not session.user.is_active:
        raise HTTPException(status_code=401, detail="登录已失效")
    return session.user


def current_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可以管理邀请码")
    return user


def _next_run(send_time: str) -> datetime:
    china_tz = timezone(timedelta(hours=8))
    now_local = datetime.now(china_tz)
    hour, minute = map(int, send_time.split(":"))
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc).replace(tzinfo=None)


def _minute_distance(first: str, second: str) -> int:
    ah, am = map(int, first.split(":"))
    bh, bm = map(int, second.split(":"))
    distance = abs((ah * 60 + am) - (bh * 60 + bm))
    return min(distance, 1440 - distance)


def _schedule_load(db: Session, send_time: str, exclude_task_id: str | None = None) -> dict:
    tasks = db.scalars(select(Task).where(Task.enabled.is_(True), Task.deleted_at.is_(None))).all()
    nearby = [task for task in tasks if task.id != exclude_task_id and _minute_distance(task.send_time, send_time) <= 15]
    count = len(nearby)
    level = "idle" if count == 0 else "light" if count == 1 else "busy" if count < 4 else "full"
    return {"send_time": send_time, "nearby_tasks": count, "capacity": 4, "available": count < 4, "level": level, "estimated_wait_minutes": count * 5}


def _recommended_schedule(db: Session, user_id: str) -> dict:
    candidates = [f"{minute // 60:02d}:{minute % 60:02d}" for minute in range(18 * 60 + 30, 22 * 60 + 31, 5)]
    ranked = []
    for send_time in candidates:
        load = _schedule_load(db, send_time)
        tie_breaker = hashlib.sha256(f"{user_id}:{send_time}".encode()).hexdigest()
        ranked.append((not load["available"], load["nearby_tasks"], tie_breaker, load))
    return min(ranked, key=lambda item: item[:3])[3]


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.scalar(select(func.count()).select_from(User))
    return {"ok": True, "email_ready": bool(settings.smtp_host and settings.smtp_from), "time": utcnow().isoformat() + "Z"}


@app.get("/api/schedule/availability")
def schedule_availability(send_time: str = Query(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"), exclude_task_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if exclude_task_id:
        own_task = db.scalar(select(Task).where(Task.id == exclude_task_id, Task.user_id == user.id, Task.deleted_at.is_(None)))
        if not own_task:
            raise HTTPException(status_code=404, detail="执行计划不存在")
    return _schedule_load(db, send_time, exclude_task_id)


@app.get("/api/schedule/recommendation")
def schedule_recommendation(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _recommended_schedule(db, user.id)


@app.post("/api/auth/register/start", status_code=202)
def register_start(payload: RegisterStart, db: Session = Depends(get_db)):
    email_ready = bool(settings.smtp_host and settings.smtp_from)
    if settings.email_verification_required and not email_ready:
        raise HTTPException(status_code=503, detail="当前部署要求邮箱验证，但邮件服务尚未配置")
    email = normalize_email(str(payload.email))
    if db.scalar(select(User).where(or_(User.username == payload.username, User.email == email))):
        raise HTTPException(status_code=409, detail="用户名或邮箱已被使用")
    invite = db.scalar(select(InviteCode).where(InviteCode.code_digest == digest(payload.invite_code.strip().upper())))
    now = utcnow()
    if not invite or not invite.active or invite.uses >= invite.max_uses or (invite.expires_at and invite.expires_at <= now):
        raise HTTPException(status_code=400, detail="邀请码无效或已用完")
    existing = db.scalar(select(PendingRegistration).where(PendingRegistration.email == email))
    if existing and existing.resend_after > now:
        wait = int((existing.resend_after - now).total_seconds()) + 1
        raise HTTPException(status_code=429, detail=f"请 {wait} 秒后重新发送")
    if existing:
        db.delete(existing)
        db.flush()
    code = random_code()
    pending = PendingRegistration(
        username=payload.username,
        email=email,
        password_hash=hash_password(payload.password),
        invite_id=invite.id,
        code_digest=digest(f"{email}:{code}"),
        code_expires_at=expires_in(minutes=10),
        resend_after=expires_in(seconds=60),
    )
    db.add(pending)
    if email_ready:
        enqueue_email(
            db,
            email,
            "火花值守邮箱验证码",
            f"请勿转发给他人。你的验证码是：{code}，10 分钟内有效。",
        )
    db.commit()
    result = {"ok": True, "expires_in": 600, "resend_after": 60}
    if not settings.email_verification_required or (settings.debug_return_codes and settings.app_env != "production"):
        result["verification_code"] = code
    return result


@app.post("/api/auth/register/verify", status_code=201)
def register_verify(payload: RegisterVerify, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    pending = db.scalar(select(PendingRegistration).where(PendingRegistration.email == email))
    now = utcnow()
    if not pending or pending.code_expires_at <= now:
        raise HTTPException(status_code=400, detail="验证码不存在或已过期")
    if pending.attempts >= 5:
        raise HTTPException(status_code=429, detail="错误次数过多，请重新获取验证码")
    if not hmac_compare(pending.code_digest, digest(f"{email}:{payload.code}")):
        pending.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="验证码错误")
    invite = db.get(InviteCode, pending.invite_id)
    if not invite or not invite.active or invite.uses >= invite.max_uses:
        raise HTTPException(status_code=400, detail="邀请码已经失效")
    is_admin = bool(settings.admin_email and email == normalize_email(settings.admin_email))
    user = User(username=pending.username, email=email, password_hash=pending.password_hash, is_admin=is_admin)
    db.add(user)
    db.flush()
    db.add(NotificationPreference(user_id=user.id, daily_incomplete=False))
    db.add(UserEntitlement(user_id=user.id, plan_tier="free", plan_period=None, expires_at=expires_in(days=FREE_TRIAL_DAYS)))
    invite.uses += 1
    db.delete(pending)
    token = _create_session(db, user.id)
    db.commit()
    _set_session_cookie(response, token)
    return {"ok": True, "user": _user_json(user, db)}


def hmac_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


def _create_session(db: Session, user_id: str) -> str:
    token = random_token()
    db.add(LoginSession(token_digest=digest(token), user_id=user_id, expires_at=expires_in(days=settings.session_days)))
    return token


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.session_secure,
        samesite="lax",
        path="/",
    )


def _plan_tier(user: User, db: Session) -> str:
    if user.is_admin:
        return "pro"
    entitlement = db.get(UserEntitlement, user.id)
    if entitlement and entitlement.expires_at and entitlement.expires_at <= utcnow():
        return "expired"
    if entitlement and entitlement.plan_tier == "pro":
        return "pro"
    return "free"


def _user_json(user: User, db: Session) -> dict:
    tier = _plan_tier(user, db)
    entitlement = db.get(UserEntitlement, user.id)
    elevated = tier == "pro" or user.is_admin
    task_limit = 5 if tier in {"free", "pro"} or user.is_admin else 0
    total_task_limit = 10 if elevated else FREE_TASK_LIMIT if tier == "free" else 0
    total_recipient_limit = 25 if elevated else FREE_RECIPIENT_LIMIT if tier == "free" else 0
    expires_at = entitlement.expires_at if not user.is_admin and entitlement and entitlement.plan_tier in {"free", "pro"} else None
    remaining_days = max(0, math.ceil((expires_at - utcnow()).total_seconds() / 86400)) if expires_at else None
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_admin": user.is_admin,
        "plan_tier": tier,
        "plan_period": entitlement.plan_period if entitlement and entitlement.plan_tier == "pro" else None,
        "plan_started_at": (entitlement.updated_at if tier == "pro" else user.created_at) if expires_at else None,
        "plan_expires_at": expires_at,
        "plan_remaining_days": remaining_days,
        "limits": {
            "accounts": 5,
            "tasks_per_account": task_limit,
            "tasks_total": total_task_limit,
            "recipients_per_task": RECIPIENTS_PER_TASK,
            "recipients_total": total_recipient_limit,
        },
    }


def _run_json(run: Run, db: Session) -> dict:
    task = db.get(Task, run.task_id)
    retry = db.scalar(select(Run).where(Run.parent_run_id == run.id).order_by(Run.scheduled_for))
    if not retry:
        retry = db.scalar(
            select(Run)
            .where(
                Run.task_id == run.task_id,
                Run.attempt == run.attempt + 1,
                Run.scheduled_for > run.scheduled_for,
                Run.scheduled_for <= run.scheduled_for + timedelta(hours=6),
            )
            .order_by(Run.scheduled_for)
        )
    return {
        "id": run.id,
        "task_id": run.task_id,
        "account_id": task.account_id if task else None,
        "task_name": task.name if task else "已删除的计划",
        "status": run.status,
        "attempt": run.attempt,
        "parent_run_id": run.parent_run_id,
        "scheduled_for": run.scheduled_for,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "next_retry_at": retry.scheduled_for if retry else None,
        "items": [
            {
                "contact_name": item.contact_name,
                "message": item.message,
                "status": item.status,
                "reason": item.reason,
            }
            for item in run.items
        ],
    }


@app.post("/api/auth/login")
def login(payload: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    identity = payload.username.strip()
    now = utcnow()
    key = _login_key(request, identity)
    if _login_is_limited(key, now):
        raise HTTPException(status_code=429, detail="登录尝试过多，请 15 分钟后再试")
    user = db.scalar(select(User).where(or_(User.username == identity, User.email == normalize_email(identity))))
    if not user or not user.is_active or not verify_password(user.password_hash, payload.password):
        _record_login_failure(key, now)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _clear_login_failures(key)
    token = _create_session(db, user.id)
    db.commit()
    _set_session_cookie(response, token)
    return {"ok": True, "user": _user_json(user, db)}


@app.post("/api/auth/password/reset/start", status_code=202)
def password_reset_start(payload: PasswordResetStart, db: Session = Depends(get_db)):
    email_ready = bool(settings.smtp_host and settings.smtp_from)
    if settings.email_verification_required and not email_ready:
        raise HTTPException(status_code=503, detail="当前部署要求邮箱验证，但邮件服务尚未配置")
    email = normalize_email(str(payload.email))
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    result = {"ok": True, "message": "如果该邮箱已注册，验证码将发送到邮箱", "expires_in": 600, "resend_after": 60}
    if not user:
        return result

    now = utcnow()
    existing = db.get(PasswordReset, user.id)
    if existing and existing.resend_after > now:
        return result
    if existing:
        db.delete(existing)
        db.flush()

    code = random_code()
    db.add(
        PasswordReset(
            user_id=user.id,
            email=email,
            code_digest=digest(f"password-reset:{user.id}:{code}"),
            code_expires_at=expires_in(minutes=10),
            resend_after=expires_in(seconds=60),
        )
    )
    if email_ready:
        enqueue_email(
            db,
            email,
            "火花值守密码重置验证码",
            f"请勿转发给他人。你的密码重置验证码是：{code}，10 分钟内有效。若非本人操作，请忽略本邮件。",
            user.id,
        )
    db.commit()
    if not settings.email_verification_required or (settings.debug_return_codes and settings.app_env != "production"):
        result["verification_code"] = code
    return result


@app.post("/api/auth/password/reset/verify")
def password_reset_verify(payload: PasswordResetVerify, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    pending = db.scalar(select(PasswordReset).where(PasswordReset.email == email))
    now = utcnow()
    if not pending or pending.code_expires_at <= now:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    if pending.attempts >= 5:
        raise HTTPException(status_code=429, detail="错误次数过多，请重新获取验证码")
    if not hmac_compare(pending.code_digest, digest(f"password-reset:{pending.user_id}:{payload.code}")):
        pending.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    user = db.get(User, pending.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    user.password_hash = hash_password(payload.password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.delete(pending)
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"ok": True}


@app.post("/api/auth/password/change")
def password_change(payload: PasswordChange, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.password)
    current_token = request.cookies.get(settings.session_cookie_name)
    query = delete(LoginSession).where(LoginSession.user_id == user.id)
    if current_token:
        query = query.where(LoginSession.token_digest != digest(current_token))
    db.execute(query)
    db.commit()
    return {"ok": True}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        db.execute(delete(LoginSession).where(LoginSession.token_digest == digest(token)))
        db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@app.get("/api/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _user_json(user, db)


def _invite_json(invite: InviteCode) -> dict:
    now = utcnow()
    return {
        "id": invite.id,
        "max_uses": invite.max_uses,
        "uses": invite.uses,
        "active": invite.active,
        "expired": bool(invite.expires_at and invite.expires_at <= now),
        "expires_at": invite.expires_at,
        "created_at": invite.created_at,
    }


@app.get("/api/admin/invites")
def list_invites(_admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    invites = db.scalars(select(InviteCode).order_by(InviteCode.created_at.desc())).all()
    return [_invite_json(invite) for invite in invites]


@app.post("/api/admin/invites", status_code=201)
def create_invite(payload: InviteCreate, _admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(5):
        raw = "SPARK-" + "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))
        code_digest = digest(raw)
        if not db.scalar(select(InviteCode).where(InviteCode.code_digest == code_digest)):
            break
    else:
        raise HTTPException(status_code=503, detail="邀请码生成失败，请稍后重试")
    invite = InviteCode(
        code_digest=code_digest,
        max_uses=payload.max_uses,
        expires_at=expires_in(days=payload.expires_in_days) if payload.expires_in_days else None,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return {**_invite_json(invite), "code": raw}


@app.patch("/api/admin/invites/{invite_id}")
def update_invite(invite_id: str, payload: InviteStatusInput, _admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    invite = db.get(InviteCode, invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    invite.active = payload.active
    db.commit()
    return _invite_json(invite)


@app.delete("/api/admin/invites/{invite_id}", status_code=204)
def delete_invite(invite_id: str, _admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    invite = db.get(InviteCode, invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    pending = db.scalar(select(func.count()).select_from(PendingRegistration).where(PendingRegistration.invite_id == invite.id))
    if invite.uses or pending:
        raise HTTPException(status_code=409, detail="已使用或正在验证的邀请码只能停用，不能删除")
    db.delete(invite)
    db.commit()


@app.get("/api/admin/users")
def list_admin_users(_admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    result = []
    for managed_user in db.scalars(select(User).order_by(User.created_at.desc())).all():
        entitlement = db.get(UserEntitlement, managed_user.id)
        accounts = []
        for account in db.scalars(select(DouyinAccount).where(DouyinAccount.user_id == managed_user.id).order_by(DouyinAccount.created_at.desc())).all():
            bound_at = db.scalar(
                select(func.min(BindingSession.finished_at)).where(
                    BindingSession.account_id == account.id,
                    BindingSession.status == "complete",
                )
            )
            tasks = db.scalars(select(Task).where(Task.account_id == account.id, Task.deleted_at.is_(None))).all()
            latest_binding = db.scalar(select(BindingSession).where(BindingSession.account_id == account.id).order_by(BindingSession.created_at.desc()))
            accounts.append({
                "id": account.id,
                "name": account.name,
                "status": account.status,
                "created_at": account.created_at,
                "bound_at": bound_at,
                "last_verified_at": account.last_verified_at,
                "tasks": len(tasks),
                "enabled_tasks": sum(1 for task in tasks if task.enabled),
                "plans": [
                    {
                        "id": task.id,
                        "name": task.name,
                        "send_time": task.send_time,
                        "enabled": task.enabled,
                        "recipients": [recipient.contact_name for recipient in task.recipients if recipient.enabled],
                    }
                    for task in tasks
                ],
                "latest_binding": ({
                    "id": latest_binding.id,
                    "status": latest_binding.status,
                    "error_message": latest_binding.error_message if latest_binding.status in {"error", "expired"} else None,
                    "created_at": latest_binding.created_at,
                    "finished_at": latest_binding.finished_at,
                } if latest_binding else None),
            })
        result.append({
            "id": managed_user.id,
            "username": managed_user.username,
            "email": managed_user.email,
            "is_admin": managed_user.is_admin,
            "is_active": managed_user.is_active,
            "created_at": managed_user.created_at,
            "plan_tier": _plan_tier(managed_user, db),
            "plan_period": entitlement.plan_period if entitlement and entitlement.plan_tier == "pro" else None,
            "plan_started_at": (entitlement.updated_at if entitlement.plan_tier == "pro" else managed_user.created_at) if entitlement and entitlement.plan_tier in {"free", "pro"} else None,
            "plan_expires_at": entitlement.expires_at if entitlement and entitlement.plan_tier in {"free", "pro"} else None,
            "plan_remaining_days": max(0, math.ceil((entitlement.expires_at - utcnow()).total_seconds() / 86400)) if entitlement and entitlement.plan_tier in {"free", "pro"} and entitlement.expires_at else None,
            "accounts": accounts,
        })
    return result


@app.get("/api/admin/accounts/{account_id}/runs/dates")
def list_admin_account_run_dates(account_id: str, _admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    if not db.get(DouyinAccount, account_id):
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    local_date = func.date(Run.scheduled_for, "+8 hours")
    return db.scalars(
        select(local_date)
        .join(Task, Run.task_id == Task.id)
        .where(Task.account_id == account_id)
        .distinct()
        .order_by(local_date.desc())
    ).all()


@app.get("/api/admin/accounts/{account_id}/runs")
def list_admin_account_runs(
    account_id: str,
    run_date: str = Query(alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    _admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
):
    if not db.get(DouyinAccount, account_id):
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    try:
        local_start = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="运行记录日期无效") from exc
    utc_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    runs = db.scalars(
        select(Run)
        .join(Task, Run.task_id == Task.id)
        .where(Task.account_id == account_id, Run.scheduled_for >= utc_start, Run.scheduled_for < utc_start + timedelta(days=1))
        .order_by(Run.scheduled_for.desc())
    ).all()
    return [_run_json(run, db) for run in runs]


@app.post("/api/admin/users/{user_id}/upgrade-pro")
def upgrade_user_to_pro(user_id: str, payload: ProUpgradeInput | None = None, _admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    managed_user = db.get(User, user_id)
    if not managed_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if managed_user.is_admin:
        raise HTTPException(status_code=409, detail="管理员账号已永久拥有 Pro 同等权益")
    if _plan_tier(managed_user, db) == "pro":
        raise HTTPException(status_code=409, detail="该用户已经是 Pro 用户")

    now = utcnow()
    entitlement = db.get(UserEntitlement, managed_user.id)
    if not entitlement:
        entitlement = UserEntitlement(user_id=managed_user.id)
        db.add(entitlement)
    entitlement.plan_tier = "pro"
    period = payload.period if payload else "year"
    entitlement.plan_period = period
    entitlement.updated_at = now
    entitlement.expires_at = now + timedelta(days={"week": 7, "month": 30, "quarter": 90, "year": 365}[period])
    db.commit()
    return _user_json(managed_user, db)


@app.post("/api/admin/bindings/{binding_id}/cancel")
def cancel_admin_binding(binding_id: str, _admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    binding = db.get(BindingSession, binding_id)
    if not binding:
        raise HTTPException(status_code=404, detail="绑定会话不存在")
    if binding.status not in {"pending", "running", "qr_ready", "verification_required", "verification_submitted"}:
        raise HTTPException(status_code=409, detail="这个绑定会话已经结束")
    binding.status = "expired"
    binding.error_message = "管理员已清理卡住的绑定会话，请用户重新发起绑定"
    binding.verification_code_ciphertext = None
    binding.finished_at = utcnow()
    account = db.get(DouyinAccount, binding.account_id)
    if account and account.status == "binding":
        account.status = "unbound"
    db.commit()
    return {"ok": True}


@app.get("/api/settings/notifications")
def get_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    pref = db.get(NotificationPreference, user.id)
    if not pref:
        pref = NotificationPreference(user_id=user.id, daily_incomplete=False)
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return {name: getattr(pref, name) for name in NotificationInput.model_fields}


@app.put("/api/settings/notifications")
def update_notifications(payload: NotificationInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    pref = db.get(NotificationPreference, user.id)
    if not pref:
        pref = NotificationPreference(user_id=user.id, daily_incomplete=False)
        db.add(pref)
    for name, value in payload.model_dump().items():
        setattr(pref, name, value)
    db.commit()
    return {"ok": True}


@app.post("/api/settings/notifications/test", status_code=202)
def test_notification(user: User = Depends(current_user), db: Session = Depends(get_db)):
    enqueue_email(
        db,
        user.email,
        "火花值守测试通知",
        "这是一封测试邮件。你能收到它，说明任务异常与账号失效提醒可以正常送达。",
        user.id,
    )
    db.commit()
    return {"ok": True}


@app.get("/api/accounts")
def list_accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    accounts = db.scalars(select(DouyinAccount).where(DouyinAccount.user_id == user.id).order_by(DouyinAccount.created_at)).all()
    result = []
    for account in accounts:
        bound_at = db.scalar(
            select(func.min(BindingSession.finished_at)).where(
                BindingSession.account_id == account.id,
                BindingSession.status == "complete",
            )
        )
        result.append({"id": account.id, "name": account.name, "status": account.status, "bound_at": bound_at, "last_verified_at": account.last_verified_at})
    return result


@app.post("/api/accounts", status_code=201)
def create_account(payload: AccountCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account_count = len(db.scalars(select(DouyinAccount).where(DouyinAccount.user_id == user.id)).all())
    if account_count >= 5:
        raise HTTPException(status_code=409, detail="每位用户最多绑定 5 个抖音账号")
    account = DouyinAccount(user_id=user.id, name=payload.name)
    db.add(account)
    db.commit()
    return {"id": account.id, "name": account.name, "status": account.status}


@app.delete("/api/accounts/{account_id}", status_code=204)
def delete_account(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id, DouyinAccount.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="抖音账号不存在")

    active_binding = db.scalar(
        select(BindingSession).where(
            BindingSession.account_id == account.id,
            BindingSession.status.in_(["pending", "running", "qr_ready", "verification_required", "verification_submitted"]),
            BindingSession.expires_at > utcnow(),
        )
    )
    active_sync = db.scalar(
        select(ContactSync).where(
            ContactSync.account_id == account.id,
            ContactSync.status.in_(["pending", "running"]),
        )
    )
    active_run = db.scalar(
        select(Run)
        .join(Task, Run.task_id == Task.id)
        .where(Task.account_id == account.id, Run.status.in_(["pending", "running"]))
    )
    if active_binding or active_sync or active_run:
        raise HTTPException(status_code=409, detail="当前账号仍有绑定、好友读取或执行任务正在进行，请结束后再删除")

    db.delete(account)
    db.commit()
    return Response(status_code=204)


@app.post("/api/accounts/{account_id}/binding", status_code=202)
def start_binding(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id, DouyinAccount.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    active = db.scalar(select(BindingSession).where(BindingSession.status.in_(["pending", "running", "qr_ready", "verification_required", "verification_submitted"]), BindingSession.expires_at > utcnow()))
    if active:
        if active.user_id == user.id and active.account_id == account_id:
            return {"id": active.id, "status": active.status, "expires_at": active.expires_at, "remaining_seconds": max(0, int((active.expires_at - utcnow()).total_seconds()))}
        raise HTTPException(status_code=409, detail="当前有其他账号正在扫码，请稍后再试")
    binding = BindingSession(account_id=account.id, user_id=user.id, expires_at=expires_in(minutes=5))
    account.status = "binding"
    db.add(binding)
    db.commit()
    return {"id": binding.id, "status": binding.status, "expires_at": binding.expires_at, "remaining_seconds": max(0, int((binding.expires_at - utcnow()).total_seconds()))}


@app.get("/api/accounts/{account_id}/binding/{binding_id}")
def binding_status(account_id: str, binding_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    binding = db.scalar(select(BindingSession).where(BindingSession.id == binding_id, BindingSession.account_id == account_id, BindingSession.user_id == user.id))
    if not binding:
        raise HTTPException(status_code=404, detail="扫码会话不存在")
    return {"id": binding.id, "status": binding.status, "qr_data_url": binding.qr_data_url, "error_message": binding.error_message, "expires_at": binding.expires_at, "remaining_seconds": max(0, int((binding.expires_at - utcnow()).total_seconds()))}


@app.post("/api/accounts/{account_id}/binding/{binding_id}/cancel")
def cancel_own_binding(account_id: str, binding_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    binding = db.scalar(select(BindingSession).where(BindingSession.id == binding_id, BindingSession.account_id == account_id, BindingSession.user_id == user.id))
    if not binding:
        raise HTTPException(status_code=404, detail="扫码会话不存在")
    if binding.status not in {"pending", "running", "qr_ready", "verification_required", "verification_submitted"}:
        raise HTTPException(status_code=409, detail="这个扫码会话已经结束")
    binding.status = "expired"
    binding.expires_at = utcnow()
    binding.qr_data_url = None
    binding.verification_code_ciphertext = None
    binding.error_message = "旧二维码已作废，请重新生成二维码"
    binding.finished_at = utcnow()
    account = db.get(DouyinAccount, account_id)
    if account and account.status == "binding":
        account.status = "unbound"
    db.commit()
    return {"ok": True}


@app.post("/api/accounts/{account_id}/binding/{binding_id}/verification", status_code=202)
def submit_binding_verification(account_id: str, binding_id: str, payload: BindingVerification, user: User = Depends(current_user), db: Session = Depends(get_db)):
    binding = db.scalar(select(BindingSession).where(BindingSession.id == binding_id, BindingSession.account_id == account_id, BindingSession.user_id == user.id))
    if not binding:
        raise HTTPException(status_code=404, detail="扫码会话不存在")
    if binding.expires_at <= utcnow():
        raise HTTPException(status_code=410, detail="扫码会话已超时，请重新发起绑定")
    if binding.status != "verification_required":
        raise HTTPException(status_code=409, detail="当前扫码会话不需要验证码")
    if binding.verification_attempts >= 3:
        raise HTTPException(status_code=429, detail="验证码尝试次数过多，请重新发起绑定")
    binding.verification_code_ciphertext = encrypt_text(payload.code)
    binding.verification_attempts += 1
    binding.status = "verification_submitted"
    binding.error_message = "验证码已提交，正在等待抖音确认"
    db.commit()
    return {"id": binding.id, "status": binding.status}


@app.post("/api/accounts/{account_id}/contacts/sync", status_code=202)
def start_contact_sync(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id, DouyinAccount.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    if account.status != "valid" or not account.state_ciphertext:
        raise HTTPException(status_code=409, detail="请先完成当前抖音账号的扫码绑定")
    active = db.scalar(select(ContactSync).where(ContactSync.account_id == account_id, ContactSync.status.in_(["pending", "running"])).order_by(ContactSync.created_at.desc()))
    if active:
        return {"id": active.id, "status": active.status}
    sync = ContactSync(account_id=account.id, user_id=user.id)
    db.add(sync)
    db.commit()
    return {"id": sync.id, "status": sync.status}


def _contact_json(contact: Contact) -> dict:
    return {
        "id": contact.id,
        "display_name": contact.display_name,
        "conversation_type": contact.conversation_type,
        "type_locked": contact.type_locked,
        "avatar_url": contact.avatar_url,
        "streak_days": contact.streak_days,
        "selected": contact.selected,
        "message": contact.planned_message,
        "last_seen_at": contact.last_seen_at,
    }


@app.get("/api/accounts/{account_id}/contacts")
def list_saved_contacts(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id, DouyinAccount.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    contacts = db.scalars(
        select(Contact)
        .where(Contact.account_id == account_id, Contact.user_id == user.id)
        .order_by(Contact.conversation_type, Contact.display_name)
    ).all()
    latest_sync = db.scalar(
        select(ContactSync)
        .where(ContactSync.account_id == account_id, ContactSync.user_id == user.id, ContactSync.status == "complete")
        .order_by(ContactSync.finished_at.desc())
    )
    return {
        "contacts": [_contact_json(contact) for contact in contacts],
        "synced_at": latest_sync.finished_at if latest_sync else None,
    }


@app.patch("/api/accounts/{account_id}/contacts/{contact_id}")
def update_contact_type(account_id: str, contact_id: str, payload: ContactTypeInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    contact = db.scalar(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.account_id == account_id,
            Contact.user_id == user.id,
        )
    )
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    contact.conversation_type = payload.conversation_type
    contact.type_locked = True
    recipients = db.scalars(
        select(TaskRecipient)
        .join(Task)
        .where(Task.account_id == account_id, Task.user_id == user.id, TaskRecipient.contact_id == contact.id)
    ).all()
    for recipient in recipients:
        recipient.conversation_type = payload.conversation_type
    db.commit()
    return _contact_json(contact)


@app.put("/api/accounts/{account_id}/contacts/selection")
def save_contact_selection(account_id: str, payload: ContactSelectionInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == account_id, DouyinAccount.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    contacts = db.scalars(select(Contact).where(Contact.account_id == account_id, Contact.user_id == user.id)).all()
    contacts_by_id = {contact.id: contact for contact in contacts}
    selected: list[tuple[Contact, str]] = []
    seen: set[str] = set()
    selected_names: set[str] = set()
    for item in payload.recipients:
        contact = contacts_by_id.get(item.contact_id or "")
        if not contact:
            raise HTTPException(status_code=400, detail="所选联系人不存在或不属于当前抖音账号")
        if contact.id in seen:
            continue
        if contact.display_name in selected_names:
            raise HTTPException(status_code=409, detail="存在同名会话，暂时不能同时加入同一个计划")
        seen.add(contact.id)
        selected_names.add(contact.display_name)
        selected.append((contact, item.message))
    for contact in contacts:
        contact.selected = False
    for contact, message in selected:
        contact.selected = True
        contact.planned_message = message
    db.commit()
    return {"contacts": [_contact_json(contact) for contact in contacts], "task_updated": False}


@app.get("/api/accounts/{account_id}/contacts/sync/{sync_id}")
def contact_sync_status(account_id: str, sync_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sync = db.scalar(select(ContactSync).where(ContactSync.id == sync_id, ContactSync.account_id == account_id, ContactSync.user_id == user.id))
    if not sync:
        raise HTTPException(status_code=404, detail="好友读取任务不存在")
    raw_contacts = json.loads(sync.contacts_json or "[]")
    contacts = [
        item if isinstance(item, dict) else {"display_name": str(item), "conversation_type": "unknown"}
        for item in raw_contacts
    ]
    return {"id": sync.id, "status": sync.status, "contacts": contacts, "error_message": sync.error_message, "finished_at": sync.finished_at}


def _task_json(task: Task) -> dict:
    return {
        "id": task.id,
        "account_id": task.account_id,
        "name": task.name,
        "send_time": task.send_time,
        "enabled": task.enabled,
        "next_run_at": task.next_run_at,
        "recipients": [
            {
                "id": item.id,
                "contact_id": item.contact_id,
                "contact_name": item.contact_name,
                "conversation_type": item.conversation_type,
                "message": item.message,
                "enabled": item.enabled,
            }
            for item in task.recipients
        ],
    }


def _task_recipients(payload: TaskCreate, account: DouyinAccount, user: User, db: Session, exclude_task_id: str | None = None) -> list[TaskRecipient]:
    recipients: list[TaskRecipient] = []
    names: set[str] = set()
    for item in payload.recipients:
        contact = None
        if item.contact_id:
            contact = db.scalar(
                select(Contact).where(
                    Contact.id == item.contact_id,
                    Contact.account_id == account.id,
                    Contact.user_id == user.id,
                )
            )
            if not contact:
                raise HTTPException(status_code=400, detail="所选联系人不存在或不属于当前抖音账号")
            duplicate_query = (
                select(TaskRecipient)
                .join(Task)
                .where(
                    Task.account_id == account.id,
                    Task.user_id == user.id,
                    Task.deleted_at.is_(None),
                    TaskRecipient.contact_id == contact.id,
                )
            )
            if exclude_task_id:
                duplicate_query = duplicate_query.where(Task.id != exclude_task_id)
            if db.scalar(duplicate_query):
                raise HTTPException(status_code=409, detail=f"{contact.display_name} 已在当前账号的其他计划中")
        contact_name = contact.display_name if contact else item.contact_name.strip()
        if contact_name in names:
            raise HTTPException(status_code=409, detail="存在同名会话，暂时不能同时加入同一个计划")
        names.add(contact_name)
        recipients.append(
            TaskRecipient(
                contact_id=contact.id if contact else None,
                contact_name=contact_name,
                conversation_type=contact.conversation_type if contact else item.conversation_type,
                message=item.message,
            )
        )
    return recipients


def _enforce_total_recipient_limit(payload: TaskCreate, user: User, db: Session, exclude_task_id: str | None = None) -> None:
    limits = _user_json(user, db)["limits"]
    query = select(func.count()).select_from(TaskRecipient).join(Task).where(Task.user_id == user.id, Task.deleted_at.is_(None))
    if exclude_task_id:
        query = query.where(Task.id != exclude_task_id)
    assigned_count = db.scalar(query) or 0
    if assigned_count + len(payload.recipients) > limits["recipients_total"]:
        raise HTTPException(
            status_code=409,
            detail=f"当前套餐所有抖音账号的计划合计最多安排 {limits['recipients_total']} 人",
        )


@app.get("/api/tasks")
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tasks = db.scalars(select(Task).where(Task.user_id == user.id, Task.deleted_at.is_(None)).order_by(Task.send_time)).all()
    return [_task_json(task) for task in tasks]


@app.post("/api/tasks", status_code=201)
def create_task(payload: TaskCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == payload.account_id, DouyinAccount.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    tier = _plan_tier(user, db)
    if tier == "expired":
        raise HTTPException(status_code=403, detail="套餐已到期，当前不能创建自动计划")
    limits = _user_json(user, db)["limits"]
    total_task_count = db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id, Task.deleted_at.is_(None))) or 0
    if total_task_count >= limits["tasks_total"]:
        raise HTTPException(status_code=409, detail=f"当前套餐所有抖音账号合计最多创建 {limits['tasks_total']} 个计划")
    account_task_count = db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id, Task.account_id == account.id, Task.deleted_at.is_(None))) or 0
    if account_task_count >= limits["tasks_per_account"]:
        raise HTTPException(status_code=409, detail=f"当前套餐每个抖音账号最多创建 {limits['tasks_per_account']} 个计划")
    _enforce_total_recipient_limit(payload, user, db)
    if not _schedule_load(db, payload.send_time)["available"]:
        raise HTTPException(status_code=409, detail="这个时间附近的服务器队列已满，请换一个时间")
    task = Task(user_id=user.id, account_id=account.id, name=payload.name, send_time=payload.send_time, enabled=payload.enabled, next_run_at=_next_run(payload.send_time))
    task.recipients = _task_recipients(payload, account, user, db)
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_json(task)


@app.put("/api/tasks/{task_id}")
def update_task(task_id: str, payload: TaskCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None)))
    if not task:
        raise HTTPException(status_code=404, detail="执行计划不存在")
    if _plan_tier(user, db) == "expired":
        raise HTTPException(status_code=403, detail="套餐已到期，当前不能更新自动计划")
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == payload.account_id, DouyinAccount.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="抖音账号不存在")
    limits = _user_json(user, db)["limits"]
    account_task_count = db.scalar(
        select(func.count()).select_from(Task).where(
            Task.user_id == user.id,
            Task.account_id == account.id,
            Task.id != task.id,
            Task.deleted_at.is_(None),
        )
    ) or 0
    if account_task_count >= limits["tasks_per_account"]:
        raise HTTPException(status_code=409, detail=f"当前套餐每个抖音账号最多创建 {limits['tasks_per_account']} 个计划")
    _enforce_total_recipient_limit(payload, user, db, task.id)
    if not _schedule_load(db, payload.send_time, task.id)["available"]:
        raise HTTPException(status_code=409, detail="这个时间附近的服务器队列已满，请换一个时间")
    task.account_id = account.id
    task.name = payload.name
    task.send_time = payload.send_time
    task.enabled = payload.enabled
    task.next_run_at = _next_run(payload.send_time)
    task.recipients.clear()
    db.flush()
    task.recipients.extend(_task_recipients(payload, account, user, db, task.id))
    db.commit()
    db.refresh(task)
    return _task_json(task)


@app.patch("/api/tasks/{task_id}")
def update_task_status(task_id: str, payload: TaskStatusInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None)))
    if not task:
        raise HTTPException(status_code=404, detail="执行计划不存在")
    if payload.enabled and _plan_tier(user, db) == "expired":
        raise HTTPException(status_code=403, detail="套餐已到期，当前不能启用自动计划")
    task.enabled = payload.enabled
    if payload.enabled:
        task.next_run_at = _next_run(task.send_time)
    db.commit()
    return _task_json(task)


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None)))
    if not task:
        raise HTTPException(status_code=404, detail="执行计划不存在")
    active = db.scalar(select(Run).where(Run.task_id == task.id, Run.status.in_(["pending", "running"])))
    if active:
        raise HTTPException(status_code=409, detail="执行计划正在排队或运行，暂时不能删除")
    task.enabled = False
    task.deleted_at = utcnow()
    db.commit()
    return Response(status_code=204)


@app.post("/api/tasks/{task_id}/runs", status_code=202)
def run_task_now(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None)))
    if not task:
        raise HTTPException(status_code=404, detail="执行计划不存在")
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="普通用户的任务只按已保存时间自动执行")
    if _plan_tier(user, db) == "expired":
        raise HTTPException(status_code=403, detail="套餐已到期，当前不能执行自动计划")
    account = db.scalar(select(DouyinAccount).where(DouyinAccount.id == task.account_id, DouyinAccount.user_id == user.id))
    if not account or account.status != "valid" or not account.state_ciphertext:
        raise HTTPException(status_code=409, detail="抖音登录未绑定或已失效")
    active = db.scalar(select(Run).where(Run.task_id == task.id, Run.status.in_(["pending", "running"])))
    if active:
        raise HTTPException(status_code=409, detail="该计划已有任务在队列中")
    run = Run(task_id=task.id, user_id=user.id, scheduled_for=utcnow())
    run.items = [RunItem(contact_name=item.contact_name, message=item.message) for item in task.recipients if item.enabled]
    if not run.items:
        raise HTTPException(status_code=400, detail="执行计划没有可用好友")
    db.add(run)
    db.commit()
    return {"id": run.id, "status": run.status, "scheduled_for": run.scheduled_for}


@app.get("/api/queue")
def queue_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    running = db.scalar(select(Run).where(Run.status == "running").order_by(Run.started_at))
    pending = db.scalars(select(Run).where(Run.status == "pending").order_by(Run.scheduled_for)).all()
    position = 1 if running and running.user_id == user.id else None
    if position is None:
        for index, item in enumerate(pending):
            if item.user_id == user.id:
                position = (1 if running else 0) + index + 1
                break
    return {
        "position": position,
        "waiting_minutes": max(0, (position - 1) * 5) if position else 0,
        "queued": len(pending) + (1 if running else 0),
        "running": bool(running),
    }


@app.get("/api/runs/dates")
def list_run_dates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    local_date = func.date(Run.scheduled_for, "+8 hours")
    return db.scalars(select(local_date).where(Run.user_id == user.id).distinct().order_by(local_date.desc())).all()


@app.get("/api/runs")
def list_runs(run_date: str | None = Query(default=None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"), user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Run).where(Run.user_id == user.id)
    if run_date:
        try:
            local_start = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="运行记录日期无效") from exc
        utc_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(Run.scheduled_for >= utc_start, Run.scheduled_for < utc_start + timedelta(days=1))
    else:
        query = query.limit(50)
    runs = db.scalars(query.order_by(Run.scheduled_for.desc())).all()
    return [_run_json(run, db) for run in runs]
