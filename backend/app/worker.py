import logging
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, func, or_, select

from .database import Base, SessionLocal, engine
from .mailer import enqueue_email
from .douyin import bind_account, list_contacts, send_batch
from .models import BindingSession, Contact, ContactSync, DouyinAccount, NotificationPreference, Run, RunItem, Task, TaskRecipient, User, UserEntitlement, utcnow
from .identity import MISSING_ID, snapshot_item, resolve_run_contact, sync_contacts, recent_contacts
from .security import decrypt_text, encrypt_text
from .config import get_settings


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spark-worker")
GLOBAL_TASK_GAP = timedelta(minutes=5)
CHINA_TZ = timezone(timedelta(hours=8))
AUTOMATION_GUARD = timedelta(minutes=6)
RUN_TIMEOUT_SECONDS = 20 * 60
RUN_STOP_GRACE_SECONDS = 10
RUN_PRESSURE_POLL_SECONDS = 2
RUN_PRESSURE_SAMPLES = 10
RUN_CRITICAL_PRESSURE_SAMPLES = 3


class BrowserResourcePressure(RuntimeError):
    pass


def _memory_pressure_snapshot() -> dict:
    """Record only resource counters, never account or browser contents."""
    snapshot = {}
    try:
        lines = Path('/proc/pressure/memory').read_text().splitlines()
        full = next(line for line in lines if line.startswith('full '))
        values = dict(pair.split('=') for pair in full.split()[1:])
        snapshot['host_full_avg10'] = float(values['avg10'])
    except (OSError, ValueError, KeyError, StopIteration):
        pass
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                snapshot['available_kib'] = int(line.split()[1])
                break
    except (OSError, ValueError):
        pass
    return snapshot


def _host_memory_under_pressure() -> bool:
    """PSI detects reclaim stalls even when MemAvailable looks sufficient."""
    snapshot = _memory_pressure_snapshot()
    return (snapshot.get('host_full_avg10', 0) >= 10
            or snapshot.get('available_kib', 128 * 1024) < 128 * 1024)


def _critical_memory_pressure(snapshot: dict) -> bool:
    return (snapshot.get('host_full_avg10', 0) >= 30
            or snapshot.get('available_kib', 128 * 1024) < 128 * 1024)


def _wait_for_run_process(process: subprocess.Popen) -> int:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    pressure_samples = 0
    critical_samples = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, RUN_TIMEOUT_SECONDS)
        try:
            return process.wait(timeout=min(RUN_PRESSURE_POLL_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            pressure_samples = pressure_samples + 1 if _host_memory_under_pressure() else 0
            snapshot = _memory_pressure_snapshot() if pressure_samples else {}
            critical_samples = critical_samples + 1 if _critical_memory_pressure(snapshot) else 0
            if pressure_samples:
                logger.warning('browser memory pressure sample=%s critical_samples=%s counters=%s',
                               pressure_samples, critical_samples, snapshot)
            if pressure_samples >= RUN_PRESSURE_SAMPLES or critical_samples >= RUN_CRITICAL_PRESSURE_SAMPLES:
                raise BrowserResourcePressure('sustained host memory pressure')


def _current_schedule(value: datetime, now: datetime) -> datetime:
    """Move stale daily schedules to today's occurrence without replaying old days."""
    local_date = (value.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)).date()
    today = (now.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)).date()
    if local_date < today:
        value += timedelta(days=(today - local_date).days)
    return value


def entitlement_is_active(db, user_id: str, now: datetime | None = None) -> bool:
    """Return whether automated work is allowed at this exact moment."""
    now = now or utcnow()
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return False
    if user.is_admin:
        return True
    entitlement = db.get(UserEntitlement, user_id)
    return bool(
        entitlement
        and entitlement.plan_tier in {"free", "pro"}
        and entitlement.expires_at
        and entitlement.expires_at > now
    )


def cancel_expired_run(db, run: Run, now: datetime | None = None) -> bool:
    """Cancel queued/claimed work when the user's entitlement has expired."""
    if entitlement_is_active(db, run.user_id, now):
        return False
    run.status = "cancelled"
    run.error_code = "PLAN_EXPIRED"
    run.error_message = "套餐有效期已结束，本次计划未执行"
    run.finished_at = now or utcnow()
    for item in run.items:
        if item.status == "pending":
            item.status = "skipped"
            item.reason = "套餐有效期已结束"
    return True


def schedule_due_tasks() -> None:
    now = utcnow()
    with SessionLocal() as db:
        eligible_users = select(User.id).outerjoin(UserEntitlement, UserEntitlement.user_id == User.id).where(
            or_(
                User.is_admin.is_(True),
                and_(
                    UserEntitlement.plan_tier.in_(["free", "pro"]),
                    UserEntitlement.expires_at.is_not(None),
                    UserEntitlement.expires_at > now,
                ),
            )
        )
        expired_due_tasks = db.scalars(
            select(Task).where(
                Task.enabled.is_(True),
                Task.next_run_at <= now,
                Task.user_id.not_in(eligible_users),
            )
        ).all()
        for task in expired_due_tasks:
            task.next_run_at = _current_schedule(task.next_run_at, now)
            if task.next_run_at <= now:
                task.next_run_at += timedelta(days=1)
        tasks = db.scalars(select(Task).where(Task.enabled.is_(True), Task.next_run_at <= now, Task.user_id.in_(eligible_users)).order_by(Task.next_run_at).limit(20)).all()
        for task in tasks:
            scheduled_for = _current_schedule(task.next_run_at, now)
            task.next_run_at = scheduled_for
            if scheduled_for > now:
                continue
            existing = db.scalar(select(Run).where(Run.task_id == task.id, Run.scheduled_for == scheduled_for))
            if not existing:
                run = Run(task_id=task.id, user_id=task.user_id, scheduled_for=scheduled_for)
                run.items = [snapshot_item(db, task, r) for r in task.recipients if r.enabled]
                db.add(run)
            task.next_run_at = scheduled_for + timedelta(days=1)
        db.commit()


def claim_one() -> str | None:
    now = utcnow()
    with SessionLocal() as db:
        stale = db.scalars(select(Run).where(Run.status == "running", Run.claimed_at < now - timedelta(minutes=20))).all()
        for item in stale:
            in_flight = [child for child in item.items if child.status == "sending"]
            if in_flight:
                for child in in_flight:
                    child.status = "uncertain"
                    child.reason = "发送过程中服务重启，无法确认结果；为避免重复未自动重试"
                    child.finished_at = now
                for child in item.items:
                    if child.status == "pending":
                        child.status = "skipped"
                        child.reason = "同批次存在待确认消息，已停止以避免重复发送"
                        child.finished_at = now
                item.status = "needs_review"
                item.error_code = "WORKER_INTERRUPTED"
                item.error_message = "发送过程中服务重启，部分结果需要人工确认"
                item.finished_at = now
                notify_failure(
                    db,
                    item,
                    failure_email_subject(db, item, "发送结果待确认"),
                    failure_email_body(db, item, "发送过程中服务重启，系统为避免重复消息已停止自动重试。"),
                    "task_failed",
                )
            else:
                # A worker can disappear before the first send (for example,
                # after hitting its memory limit). Requeueing that same run
                # would repeat the resource failure indefinitely.
                item.status = "failed"
                item.error_code = "WORKER_INTERRUPTED"
                item.error_message = "浏览器任务异常中断，本次没有自动重试"
                item.finished_at = now
                for child in item.items:
                    if child.status == "pending":
                        child.status = "skipped"
                        child.reason = "浏览器任务异常中断，已停止后续发送"
                        child.finished_at = now
                notify_failure(
                    db,
                    item,
                    failure_email_subject(db, item, "发送任务已停止"),
                    failure_email_body(db, item, item.error_message),
                    "task_failed",
                )
            _settle_retry_ancestors(db, item)
        if stale:
            db.commit()
        resource_waits = db.scalars(select(Run).where(
            Run.status == "pending", Run.error_code == "RESOURCE_RETRY_WAIT"
        )).all()
        for item in resource_waits:
            if now >= _resource_retry_deadline(db, item):
                item.status = "failed"
                item.error_code = "RESOURCE_RETRY_EXPIRED"
                item.error_message = "当天内存压力持续未恢复，已停止跨日补发，避免发送过期消息"
                item.finished_at = now
                for child in item.items:
                    if child.status == "pending":
                        child.status = "failed"
                        child.reason = item.error_message
                        child.finished_at = now
                notify_failure(db, item, failure_email_subject(db, item, "当天计划未完成"),
                               failure_email_body(db, item, item.error_message), "task_failed")
                _settle_retry_ancestors(db, item)
        if resource_waits:
            db.commit()
        expired_pending = db.scalars(select(Run).where(Run.status == "pending", Run.scheduled_for <= now)).all()
        expired_changed = False
        for item in expired_pending:
            expired_changed = cancel_expired_run(db, item, now) or expired_changed
        if expired_changed:
            db.commit()
        running = db.scalar(select(Run).where(Run.status == "running"))
        if running:
            return None
        recent = db.scalar(
            select(Run)
            .join(RunItem, RunItem.run_id == Run.id)
            .where(Run.finished_at.is_not(None), RunItem.status == "submitted")
            .order_by(Run.finished_at.desc())
        )
        if recent and recent.finished_at and recent.finished_at + GLOBAL_TASK_GAP > now:
            return None
        run = db.scalar(select(Run).where(Run.status == "pending", Run.scheduled_for <= now).order_by(Run.scheduled_for))
        if not run:
            return None
        run.status = "running"
        run.claimed_at = now
        run.started_at = now
        db.commit()
        return run.id


def process_binding() -> bool:
    now = utcnow()
    with SessionLocal() as db:
        expired = db.scalars(select(BindingSession).where(BindingSession.status.in_(["pending", "running", "qr_ready", "verification_required", "verification_submitted"]), BindingSession.expires_at <= now)).all()
        for item in expired:
            item.status = "expired"
            item.error_message = "扫码会话已超时"
            item.finished_at = now
            account = db.get(DouyinAccount, item.account_id)
            if account and account.status == "binding":
                account.status = "unbound"
        binding = db.scalar(select(BindingSession).where(BindingSession.status == "pending", BindingSession.expires_at > now).order_by(BindingSession.created_at))
        if not binding:
            db.commit()
            return False
        binding.status = "running"
        binding_id = binding.id
        account_id = binding.account_id
        db.commit()

    def on_qr(data_url: str) -> None:
        with SessionLocal() as qr_db:
            current = qr_db.get(BindingSession, binding_id)
            if current:
                current.qr_data_url = data_url
                current.status = "qr_ready"
                qr_db.commit()

    def request_verification_code(challenge: str) -> str | None:
        with SessionLocal() as challenge_db:
            current = challenge_db.get(BindingSession, binding_id)
            if not current:
                return None
            current.status = "verification_required"
            current.error_message = f"网站已自动选择接收{challenge}并触发短信，请输入你本人收到的验证码。"
            challenge_db.commit()
        while True:
            time.sleep(1)
            with SessionLocal() as code_db:
                current = code_db.get(BindingSession, binding_id)
                if not current or current.expires_at <= utcnow():
                    return None
                if current.verification_code_ciphertext:
                    code = decrypt_text(current.verification_code_ciphertext)
                    current.verification_code_ciphertext = None
                    # Keep the externally visible state explicit while the
                    # worker types and submits the code in Douyin. Returning to
                    # `running` made the UI look as if nothing happened.
                    current.status = "verification_submitted"
                    current.error_message = "验证码已收到，正在向抖音提交并确认登录"
                    code_db.commit()
                    return code

    def should_cancel() -> bool:
        with SessionLocal() as cancel_db:
            current = cancel_db.get(BindingSession, binding_id)
            return not current or current.status == "expired" or current.expires_at <= utcnow()

    try:
        state_json = bind_account(on_qr, request_verification_code, should_cancel, profile_key=account_id)
        with SessionLocal() as db:
            binding = db.get(BindingSession, binding_id)
            account = db.get(DouyinAccount, account_id)
            if binding and account:
                account.state_ciphertext = encrypt_text(state_json)
                account.status = "valid"
                account.last_verified_at = utcnow()
                binding.status = "complete"
                binding.qr_data_url = None
                binding.error_message = None
                binding.finished_at = utcnow()
                db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            binding = db.get(BindingSession, binding_id)
            account = db.get(DouyinAccount, account_id)
            if binding:
                if binding.status != "expired":
                    binding.status = "error"
                    binding.error_message = str(exc)[:500]
                    binding.finished_at = utcnow()
            if account and (not binding or binding.status != "expired"):
                account.status = "unbound"
            db.commit()
    return True


def process_contact_sync() -> bool:
    with SessionLocal() as db:
        sync = db.scalar(select(ContactSync).where(ContactSync.status == "pending").order_by(ContactSync.created_at))
        if not sync:
            return False
        sync.status = "running"
        sync_id = sync.id
        account_id = sync.account_id
        db.commit()
    try:
        with SessionLocal() as db:
            account = db.get(DouyinAccount, account_id)
            state_json = decrypt_text(account.state_ciphertext) if account and account.state_ciphertext else ""
        if not state_json:
            raise RuntimeError("抖音账号尚未绑定或登录已失效")
        contacts, error, refreshed_state = list_contacts(state_json, profile_key=account_id)
        with SessionLocal() as db:
            sync = db.get(ContactSync, sync_id)
            account = db.get(DouyinAccount, account_id)
            saved_contacts: list[dict] = []
            if sync and not error:
                sync_contacts(db, account_id, sync.user_id, contacts)
                saved = recent_contacts(db, account_id, sync.user_id, sync.created_at)
                saved_contacts = [{"id": c.id, "display_name": c.display_name,
                    "conversation_type": c.conversation_type, "selected": c.selected,
                    "message": c.planned_message, "douyin_id": c.douyin_id,
                    "identity_ready": bool(c.external_id and not c.identity_conflict),
                    "last_seen_at": c.last_seen_at.isoformat() + "Z"} for c in saved]
            if sync:
                sync.contacts_json = json.dumps(saved_contacts if not error else contacts, ensure_ascii=False)
                sync.status = "complete" if not error else "error"
                sync.error_message = error
                sync.finished_at = utcnow()
            if error and error.startswith("logged_out:") and account:
                account.status = "invalid"
            elif account and refreshed_state:
                account.state_ciphertext = encrypt_text(refreshed_state)
                account.last_verified_at = utcnow()
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            sync = db.get(ContactSync, sync_id)
            if sync:
                sync.status = "error"
                sync.error_message = str(exc)[:500]
                sync.finished_at = utcnow()
                db.commit()
    return True


def notify_failure(db, run: Run, subject: str, body: str, preference: str) -> None:
    user = db.get(User, run.user_id)
    pref = db.get(NotificationPreference, run.user_id)
    admin_email = get_settings().task_failure_admin_email.strip()
    user_enabled = bool(user and pref and pref.enabled and getattr(pref, preference, False))
    event_key = f"run-failure:{run.id}:{preference}"
    if admin_email:
        admin_body = "\n".join([
            f"网站用户：{user.username if user else '已删除用户'}",
            f"用户邮箱：{user.email if user else '未知'}",
            f"运行记录：{run.id}", f"失败代码：{run.error_code or run.status}", "", body,
        ])
        enqueue_email(db, admin_email, f"[管理通知] {user.username if user else '未知用户'} · {subject}",
            admin_body, user.id if user else None, dedup_key=event_key+":admin")
    if user_enabled and (not admin_email or user.email.casefold() != admin_email.casefold()):
        enqueue_email(db, user.email, subject, body, user.id, dedup_key=event_key+":user")


def failure_email_body(db, run: Run, intro: str) -> str:
    task = db.get(Task, run.task_id)
    account = db.get(DouyinAccount, task.account_id) if task else None
    incomplete = [item for item in run.items if item.status != "submitted"]
    lines = [
        intro,
        "",
        f"抖音账号：{account.name if account else '已删除的账号'}",
        f"执行计划：{task.name if task else '已删除的计划'}",
        f"执行时间：{(run.scheduled_for + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')}（北京时间）",
        "",
        "未发送好友：",
    ]
    if incomplete:
        lines.extend(f"- {item.contact_name}：{item.reason or '未记录详细原因'}" for item in incomplete)
    else:
        lines.append("- 未记录到未发送好友，请登录网站查看运行记录")
    lines.extend(["", "请登录火花值守检查好友状态或重新同步会话。邮件不会展示消息正文、抖音号、UID 或会话 ID。"])
    return "\n".join(lines)


def failure_email_subject(db, run: Run, suffix: str) -> str:
    task = db.get(Task, run.task_id)
    account = db.get(DouyinAccount, task.account_id) if task else None
    return f"火花值守：{account.name if account else '账号'} · {task.name if task else '计划'} {suffix}"


def _create_retry(db, run: Run, items: list[RunItem]) -> None:
    if not items or any(item.status != "failed" for item in items):
        raise ValueError("A retry requires confirmed failed items")
    retry = Run(
        task_id=run.task_id,
        user_id=run.user_id,
        parent_run_id=run.id,
        scheduled_for=utcnow() + GLOBAL_TASK_GAP,
        status="pending",
        attempt=run.attempt + 1,
    )
    retry.items = [RunItem(contact_name=item.contact_name, message=item.message, recipient_id=item.recipient_id, contact_id=item.contact_id, external_id=item.external_id) for item in items]
    db.add(retry)
    run.status = "retrying"


def _resource_retry_deadline(db, run: Run) -> datetime:
    root = run
    seen = {root.id}
    while root.parent_run_id:
        parent = db.get(Run, root.parent_run_id)
        if not parent or parent.task_id != run.task_id:
            break
        if parent.id in seen:
            raise ValueError("Cyclic retry ancestry")
        seen.add(parent.id)
        root = parent
    # Datetimes in the database are UTC without tzinfo. Never replay yesterday.
    return (root.scheduled_for + timedelta(hours=8)).replace(
        hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, hours=-8)


def _create_resource_retry(db, run: Run, items: list[RunItem]) -> bool:
    if not items or any(item.status != "failed" for item in items):
        raise ValueError("Resource recovery requires confirmed unsent items")
    now = utcnow()
    task = db.get(Task, run.task_id)
    user = db.get(User, run.user_id)
    if not task or not task.enabled or task.deleted_at or not user or not user.is_active or not entitlement_is_active(db, run.user_id, now):
        return False
    count = 1
    ancestor = run
    while ancestor.parent_run_id and count < 4:
        ancestor = db.get(Run, ancestor.parent_run_id)
        if not ancestor or ancestor.error_code != "RUN_RESOURCE_PRESSURE":
            break
        count += 1
    delay = timedelta(minutes=min(30, 5 * 2 ** (count - 1)))
    retry_at = now + delay
    if retry_at >= _resource_retry_deadline(db, run):
        return False
    existing = db.scalar(select(Run).where(Run.parent_run_id == run.id))
    if existing:
        return existing.status in {"pending", "running", "retrying"}
    retry = Run(task_id=run.task_id, user_id=run.user_id, parent_run_id=run.id,
                scheduled_for=retry_at, status="pending", attempt=run.attempt,
                error_code="RESOURCE_RETRY_WAIT",
                error_message=f"内存保护暂停，{int(delay.total_seconds() / 60)} 分钟后自动恢复未发送项；不消耗发送失败次数")
    retry.items = [RunItem(contact_name=item.contact_name, message=item.message,
                          recipient_id=item.recipient_id, contact_id=item.contact_id,
                          external_id=item.external_id) for item in items]
    db.add(retry)
    run.status = "retrying"
    return True


def _settle_retry_ancestors(db, run: Run) -> None:
    if run.status == "retrying":
        return
    parent_id = run.parent_run_id
    while parent_id:
        parent = db.get(Run, parent_id)
        if not parent:
            break
        if run.status == "success":
            parent.status = "recovered"
            parent.error_code = None
            parent.error_message = f"第 {run.attempt} 次尝试已成功"
        else:
            parent.status = run.status
            parent.error_code = run.error_code
            parent.error_message = run.error_message
        parent_id = parent.parent_run_id


def task_due_for_daily_report(task: Task, utc_start: datetime) -> bool:
    try:
        hour, minute = (int(part) for part in task.send_time.split(":", 1))
    except (AttributeError, TypeError, ValueError):
        return False
    scheduled_at = utc_start + timedelta(hours=hour, minutes=minute)
    return task.created_at <= scheduled_at


def process_daily_notifications() -> None:
    now = utcnow()
    local_now = now.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)
    if (local_now.hour, local_now.minute) < (23, 20):
        return
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_start = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    utc_end = utc_start + timedelta(days=1)
    date_key = local_now.strftime("%Y-%m-%d")
    with SessionLocal() as db:
        preferences = db.scalars(select(NotificationPreference).where(NotificationPreference.enabled.is_(True))).all()
        for pref in preferences:
            user = db.get(User, pref.user_id)
            if not user:
                continue
            enabled_tasks = db.scalars(select(Task).where(Task.user_id == user.id, Task.enabled.is_(True))).all()
            # A plan created after today's send time starts tomorrow and must
            # not be reported as an unfinished plan tonight.
            tasks = [task for task in enabled_tasks if task_due_for_daily_report(task, utc_start)]
            if not tasks:
                continue
            completed: set[str] = set()
            runs = db.scalars(
                select(Run).where(
                    Run.user_id == user.id,
                    Run.scheduled_for >= utc_start,
                    Run.scheduled_for < utc_end,
                    Run.status.in_(["success", "recovered"]),
                )
            ).all()
            for run in runs:
                if run.items and all(item.status == "submitted" for item in run.items):
                    completed.add(run.task_id)
            incomplete = [task for task in tasks if task.id not in completed]
            if incomplete and pref.daily_incomplete:
                names = "、".join(task.name for task in incomplete)
                enqueue_email(
                    db,
                    user.email,
                    f"火花值守：今日有 {len(incomplete)} 个计划未完成",
                    f"截至 23:20，以下计划尚未全部完成：{names}。请登录网站查看具体账号、好友和失败原因。",
                    user.id,
                    f"daily-incomplete:{user.id}:{date_key}",
                )
            elif not incomplete and pref.daily_summary:
                enqueue_email(
                    db,
                    user.email,
                    f"火花值守：今日 {len(tasks)} 个计划已完成",
                    f"今天的 {len(tasks)} 个自动计划均已完成。你可以登录网站查看每位好友的发送记录。",
                    user.id,
                    f"daily-summary:{user.id}:{date_key}",
                )
        db.commit()


def automation_due_soon() -> bool:
    now = utcnow()
    with SessionLocal() as db:
        pending_at = db.scalar(select(func.min(Run.scheduled_for)).where(Run.status == "pending"))
        task_at = db.scalar(select(func.min(Task.next_run_at)).where(Task.enabled.is_(True)))
    return any(value and value <= now + AUTOMATION_GUARD for value in (pending_at, task_at))


def execute_run(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if not run:
            return
        if cancel_expired_run(db, run):
            _settle_retry_ancestors(db, run)
            db.commit()
            return
        task = db.get(Task, run.task_id)
        if not task or not task.enabled or task.deleted_at:
            run.status = "cancelled"
            run.error_code = "TASK_DISABLED"
            run.error_message = "计划已停用或删除，已取消后续恢复"
            run.finished_at = utcnow()
            for item in run.items:
                if item.status == "pending":
                    item.status = "skipped"
                    item.reason = run.error_message
            _settle_retry_ancestors(db, run)
            db.commit()
            return
        account = db.get(DouyinAccount, task.account_id)
        if not account or account.status != "valid" or not account.state_ciphertext:
            run.status = "needs_account"
            run.error_code = "ACCOUNT_NOT_READY"
            run.error_message = "抖音账号尚未完成扫码绑定或登录已失效"
            run.finished_at = utcnow()
            notify_failure(db, run, failure_email_subject(db, run, "需要重新绑定"), failure_email_body(db, run, "自动任务未执行，因为抖音账号尚未绑定或登录已失效。"), "login_expired")
            _settle_retry_ancestors(db, run)
            db.commit()
            return
        if not run.items:
            run.status = "failed"
            run.error_code = "EMPTY_RUN"
            run.error_message = "运行明细为空，未执行发送"
            run.finished_at = utcnow()
            notify_failure(db, run, failure_email_subject(db, run, "任务未完成"), failure_email_body(db, run, run.error_message), "task_failed")
            _settle_retry_ancestors(db, run)
            db.commit()
            return
        logger.info("run started: run=%s attempt=%s items=%s", run.id, run.attempt, len(run.items))
        recipients = []
        for item in run.items:
            if item.status != "pending":
                continue
            if run.error_code == "RESOURCE_RETRY_WAIT":
                recipient = db.get(TaskRecipient, item.recipient_id) if item.recipient_id else None
                if not recipient or not recipient.enabled or recipient.task_id != task.id or recipient.contact_id != item.contact_id:
                    item.status = "skipped"
                    item.reason = "等待恢复期间好友已移除或停用，未发送"
                    item.finished_at = utcnow()
                    continue
            contact = resolve_run_contact(db, task, item)
            if not contact:
                item.status = "needs_selection"
                item.reason = MISSING_ID
                item.finished_at = utcnow()
                continue
            recipients.append((contact.display_name, item.message, item.external_id, item.id))
        db.commit()
        try:
            state_json = decrypt_text(account.state_ciphertext)
        except Exception:
            account.status = "invalid"
            run.status = "needs_login"
            run.error_code = "STATE_DECRYPT_FAILED"
            run.error_message = "保存的登录状态无法解密，请重新扫码绑定"
            run.finished_at = utcnow()
            notify_failure(db, run, failure_email_subject(db, run, "需要重新绑定"), failure_email_body(db, run, "保存的抖音登录状态无法读取，请重新扫码绑定。"), "login_expired")
            _settle_retry_ancestors(db, run)
            db.commit()
            return

    def on_before_send(item_id: str) -> None:
        with SessionLocal() as item_db:
            item = item_db.scalar(select(RunItem).where(RunItem.run_id == run_id, RunItem.id == item_id))
            if not item or item.status != "pending":
                raise RuntimeError(f"发送明细状态异常：{item_id}")
            item.status = "sending"
            item.started_at = utcnow()
            item_db.commit()

    def on_result(result) -> None:
        with SessionLocal() as item_db:
            item = item_db.scalar(select(RunItem).where(RunItem.run_id == run_id, RunItem.id == result.item_id))
            if not item:
                raise RuntimeError(f"发送明细不存在：{result.contact_name}")
            item.status = result.status
            item.reason = result.reason or None
            item.finished_at = utcnow()
            item_db.commit()

    refreshed_state = ""
    try:
        results, fatal, refreshed_state = send_batch(state_json, recipients, on_before_send, on_result, profile_key=account.id)
    except Exception as exc:
        logger.exception("browser execution failed for run %s", run_id)
        results, fatal = [], "transient:" + str(exc)[:400]

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        task = db.get(Task, run.task_id)
        account = db.get(DouyinAccount, task.account_id)
        if account and refreshed_state:
            account.state_ciphertext = encrypt_text(refreshed_state)
            account.last_verified_at = utcnow()
        result_map = {item.item_id: item for item in results}
        for item in run.items:
            result = result_map.get(item.id)
            if result:
                item.status = result.status
                item.reason = result.reason or None
                item.finished_at = item.finished_at or utcnow()
            elif fatal:
                if item.status == "sending":
                    item.status = "uncertain"
                    item.reason = "发送后未能保存确认结果；为避免重复未自动重试"
                elif item.status == "pending":
                    if fatal.startswith(("logged_out:", "security_challenge:")):
                        item.status = "skipped"
                        item.reason = "批次因账号或安全状态停止"
                    else:
                        item.status = "failed"
                        item.reason = "浏览器页面或连接异常，未开始发送"
                item.finished_at = item.finished_at or utcnow()
            elif item.status in {"pending", "sending"}:
                item.status, item.reason = (
                    ("uncertain", "浏览器未返回确认结果；为避免重复未自动重试")
                    if item.status == "sending" else
                    ("failed", "浏览器未处理该明细，未开始发送")
                )
                item.finished_at = item.finished_at or utcnow()
        run.finished_at = utcnow()

        if fatal and not fatal.startswith(("logged_out:", "security_challenge:")):
            for item in run.items:
                if item.status == "pending":
                    item.status = "failed"
                    item.reason = fatal.partition(":")[2] or fatal
            uncertain = [item for item in run.items if item.status == "uncertain"]
            failed = [item for item in run.items if item.status == "failed"]
            if any(item.status == "needs_selection" for item in run.items):
                run.status = "needs_review"
                run.error_code = "CONTACT_IDENTITY_REQUIRED"
                run.error_message = MISSING_ID
            elif uncertain:
                run.status = "needs_review"
                run.error_code = "DELIVERY_UNCERTAIN"
                run.error_message = "服务异常时部分发送结果无法确认，为避免重复没有自动重试"
                notify_failure(db, run, failure_email_subject(db, run, "发送结果待确认"), failure_email_body(db, run, run.error_message), "task_failed")
            elif all(item.status == "submitted" for item in run.items):
                run.status = "success"
            elif failed and run.attempt < 3 and entitlement_is_active(db, run.user_id):
                _create_retry(db, run, failed)
            else:
                run.status = "failed"
                run.error_code = "RETRIES_EXHAUSTED" if run.attempt >= 3 else "BROWSER_EXECUTION_FAILED"
                run.error_message = "浏览器执行连续失败 3 次" if run.attempt >= 3 else "浏览器任务未完成，已停止重试"
                notify_failure(db, run, failure_email_subject(db, run, "任务未完成"), failure_email_body(db, run, run.error_message), "task_failed")
        elif fatal and fatal.startswith("logged_out:"):
            account.status = "invalid"
            run.status = "needs_login"
            run.error_code = "LOGIN_EXPIRED"
            run.error_message = fatal.split(":", 1)[1]
            notify_failure(db, run, failure_email_subject(db, run, "登录已失效"), failure_email_body(db, run, "抖音登录状态已经失效，请重新扫码绑定。"), "login_expired")
        elif fatal and fatal.startswith("security_challenge:"):
            account.status = "challenge"
            run.status = "security_challenge"
            run.error_code = "SECURITY_CHALLENGE"
            run.error_message = fatal.split(":", 1)[1]
            notify_failure(db, run, failure_email_subject(db, run, "需要安全验证"), failure_email_body(db, run, "任务遇到抖音安全验证，自动执行已暂停。"), "security_challenge")
        else:
            retry_items = [item for item in run.items if item.status == "failed"]
            submitted = [item for item in run.items if item.status == "submitted"]
            uncertain = [item for item in run.items if item.status == "uncertain"]
            if any(item.status == "needs_selection" for item in run.items):
                run.status = "needs_review"
                run.error_code = "CONTACT_IDENTITY_REQUIRED"
                run.error_message = MISSING_ID
            elif uncertain:
                run.status = "needs_review"
                run.error_code = "DELIVERY_UNCERTAIN"
                run.error_message = "部分发送结果无法确认，为避免重复没有自动重试"
                notify_failure(db, run, failure_email_subject(db, run, "发送结果待确认"), failure_email_body(db, run, run.error_message), "task_failed")
            elif retry_items and run.attempt < 3 and entitlement_is_active(db, run.user_id):
                _create_retry(db, run, retry_items)
            elif retry_items:
                run.status = "failed"
                run.error_code = "RETRIES_EXHAUSTED"
                run.error_message = "连续执行 3 次仍有好友未发送"
                notify_failure(db, run, failure_email_subject(db, run, "连续失败 3 次"), failure_email_body(db, run, "你的续火任务连续执行 3 次仍未完成，已停止自动重试。"), "task_failed")
            else:
                run.status = "success" if submitted else "skipped"
        if run.status == "success":
            run.error_code = None
            run.error_message = None
        if run.status in {"failed", "needs_login", "needs_account", "security_challenge", "needs_review"}:
            preference = "security_challenge" if run.status == "security_challenge" else (
                "login_expired" if run.status in {"needs_login", "needs_account"} else "task_failed")
            notify_failure(db, run, failure_email_subject(db, run, "任务未完成"),
                failure_email_body(db, run, run.error_message or "任务未完成，请查看运行记录。"), preference)
        _settle_retry_ancestors(db, run)
        db.commit()


def settle_interrupted_run(run_id: str, error_code: str) -> None:
    """Stop an interrupted batch without risking duplicate messages."""
    now = utcnow()
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if not run or run.status != "running":
            return
        may_have_sent = False
        for item in run.items:
            if item.status == "sending":
                may_have_sent = True
                item.status = "uncertain"
                item.reason = "浏览器任务中断，发送结果无法确认；为避免重复未自动重试"
                item.finished_at = now
            elif item.status in {"submitted", "uncertain"}:
                may_have_sent = True
            elif item.status == "pending":
                item.status = "failed" if error_code == "RUN_RESOURCE_PRESSURE" else "skipped"
                item.reason = ("内存压力暂停，尚未开始发送" if error_code == "RUN_RESOURCE_PRESSURE"
                               else "同批次浏览器任务中断，已停止后续发送")
                item.finished_at = now
        run.status = "needs_review" if may_have_sent else "failed"
        run.error_code = error_code
        run.error_message = {
            "RUN_TIMEOUT": "浏览器任务超时，已停止；请核对发送结果",
            "RUN_RESOURCE_PRESSURE": "服务器持续内存等待，已停止浏览器以保护网站；请核对发送结果",
        }.get(error_code, "浏览器进程异常退出，已停止；请核对发送结果")
        run.finished_at = now
        if error_code == "RUN_RESOURCE_PRESSURE" and not any(i.status == "uncertain" for i in run.items):
            retry_items = [i for i in run.items if i.status == "failed"]
            if retry_items and _create_resource_retry(db, run, retry_items):
                run.error_message = "内存保护已暂停浏览器；当天按退避间隔自动恢复未发送项，不消耗普通失败重试次数"
                logger.info('pressure recovery queued: run=%s attempt=%s items=%s',
                            run.id, run.attempt, len(retry_items))
                db.commit()
                return
            if run.items and all(i.status == "submitted" for i in run.items):
                run.status = "success"
                run.error_code = None
                run.error_message = None
                _settle_retry_ancestors(db, run)
                db.commit()
                return
            run.status = "failed"
        notify_failure(
            db,
            run,
            failure_email_subject(db, run, "发送任务已停止"),
            failure_email_body(db, run, run.error_message),
            "task_failed",
        )
        _settle_retry_ancestors(db, run)
        db.commit()


def _stop_run_process_group(process: subprocess.Popen) -> bool:
    """Terminate the browser subprocess tree, including Chromium children."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=RUN_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=RUN_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return False
    return True


def execute_run_isolated(run_id: str) -> None:
    """Bound a browser run independently of Playwright's per-call timeouts."""
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.worker", "--execute-run", run_id],
            start_new_session=True,
        )
    except OSError:
        logger.exception("could not start browser run %s", run_id)
        settle_interrupted_run(run_id, "WORKER_EXITED")
        return
    try:
        exit_code = _wait_for_run_process(process)
    except BrowserResourcePressure:
        logger.error("browser run stopped for sustained host memory pressure: %s", run_id)
        if not _stop_run_process_group(process):
            raise RuntimeError("browser process group did not stop after resource pressure")
        settle_interrupted_run(run_id, "RUN_RESOURCE_PRESSURE")
        return
    except subprocess.TimeoutExpired:
        logger.error("browser run exceeded %ss: %s", RUN_TIMEOUT_SECONDS, run_id)
        if not _stop_run_process_group(process):
            raise RuntimeError("browser process group did not stop after SIGKILL")
        settle_interrupted_run(run_id, "RUN_TIMEOUT")
        return
    if exit_code != 0:
        logger.error("browser run exited with code %s: %s", exit_code, run_id)
        if not _stop_run_process_group(process):
            raise RuntimeError("browser process group did not stop after child exit")
        settle_interrupted_run(run_id, "WORKER_EXITED")
    else:
        # A child returning without settling the run is also an abnormal exit.
        with SessionLocal() as db:
            run = db.get(Run, run_id)
            still_running = bool(run and run.status == "running")
        if still_running:
            settle_interrupted_run(run_id, "WORKER_EXITED")


def main() -> None:
    Base.metadata.create_all(engine)
    logger.info("single-browser worker started")
    pressure_paused = False
    while True:
        run_id = None
        try:
            schedule_due_tasks()
            process_daily_notifications()
            if _host_memory_under_pressure():
                if not pressure_paused:
                    logger.warning("browser work paused until host memory pressure clears: counters=%s",
                                   _memory_pressure_snapshot())
                pressure_paused = True
                time.sleep(RUN_PRESSURE_POLL_SECONDS)
                continue
            if pressure_paused:
                logger.info("host memory pressure cleared; browser queue resumed")
                pressure_paused = False
            run_id = claim_one()
            if run_id:
                execute_run_isolated(run_id)
                if os.getenv('SPARK_RECYCLE_AFTER_RUN') == '1':
                    # Docker restart + init closes the entire PID namespace,
                    # including browser processes that create their own group.
                    logger.info("browser run settled; recycling worker container")
                    return
            elif not automation_due_soon():
                if not process_binding():
                    process_contact_sync()
        except Exception:
            logger.exception("worker loop failed")
            if run_id and os.getenv('SPARK_RECYCLE_AFTER_RUN') == '1':
                logger.error("browser supervisor failed; recycling worker container")
                return
        time.sleep(10)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        main()
    elif len(sys.argv) == 3 and sys.argv[1] == "--execute-run":
        execute_run(sys.argv[2])
    else:
        raise SystemExit(2)
