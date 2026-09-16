import logging
import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select

from .database import Base, SessionLocal, engine
from .mailer import enqueue_email
from .douyin import bind_account, list_contacts, send_batch
from .models import BindingSession, Contact, ContactSync, DouyinAccount, NotificationPreference, Run, RunItem, Task, TaskRecipient, User, UserEntitlement, utcnow
from .security import decrypt_text, encrypt_text


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spark-worker")
GLOBAL_TASK_GAP = timedelta(minutes=5)
CHINA_TZ = timezone(timedelta(hours=8))
AUTOMATION_GUARD = timedelta(minutes=6)


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
    if user and user.is_admin:
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
                run.items = [RunItem(contact_name=r.contact_name, message=r.message) for r in task.recipients if r.enabled]
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
                item.status = "pending"
                item.claimed_at = None
                item.started_at = None
        if stale:
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
                planned_by_name = {
                    recipient.contact_name: recipient
                    for recipient in db.scalars(
                        select(TaskRecipient).join(Task).where(Task.account_id == account_id, Task.user_id == sync.user_id)
                    ).all()
                }
                for raw in contacts:
                    item = raw if isinstance(raw, dict) else {"display_name": str(raw), "conversation_type": "unknown"}
                    name = str(item.get("display_name") or "").strip()
                    if not name:
                        continue
                    conversation_type = str(item.get("conversation_type") or "unknown")
                    if conversation_type not in {"friend", "group", "unknown"}:
                        conversation_type = "unknown"
                    source_key = str(item.get("source_key") or item.get("external_id") or f"name:{name}")[:240]
                    contact = db.scalar(select(Contact).where(Contact.account_id == account_id, Contact.source_key == source_key))
                    if not contact and item.get("external_id"):
                        contact = db.scalar(
                            select(Contact).where(
                                Contact.account_id == account_id,
                                Contact.display_name == name,
                                Contact.external_id.is_(None),
                            )
                        )
                        if contact:
                            contact.source_key = source_key
                    if not contact:
                        contact = Contact(account_id=account_id, user_id=sync.user_id, source_key=source_key, display_name=name)
                        db.add(contact)
                    contact.display_name = name
                    if not contact.type_locked:
                        contact.conversation_type = conversation_type
                    contact.external_id = str(item.get("external_id") or "")[:240] or None
                    contact.avatar_url = str(item.get("avatar_url") or "")[:2000] or None
                    streak = item.get("streak_days")
                    contact.streak_days = int(streak) if isinstance(streak, (int, float)) else None
                    contact.last_seen_at = utcnow()
                    planned = planned_by_name.get(name)
                    if planned and not contact.selected:
                        contact.selected = True
                        contact.planned_message = planned.message
                    db.flush()
                    saved_contacts.append({
                        "id": contact.id,
                        "display_name": contact.display_name,
                        "conversation_type": contact.conversation_type,
                        "avatar_url": contact.avatar_url,
                        "streak_days": contact.streak_days,
                        "selected": contact.selected,
                        "message": contact.planned_message,
                        "last_seen_at": contact.last_seen_at.isoformat() + "Z",
                    })
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
    if user and pref and pref.enabled and getattr(pref, preference, False):
        enqueue_email(db, user.email, subject, body, user.id)


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
    retry = Run(
        task_id=run.task_id,
        user_id=run.user_id,
        parent_run_id=run.id,
        scheduled_for=utcnow() + GLOBAL_TASK_GAP,
        status="pending",
        attempt=run.attempt + 1,
    )
    retry.items = [RunItem(contact_name=item.contact_name, message=item.message) for item in items]
    db.add(retry)
    run.status = "retrying"


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
            db.commit()
            return
        task = db.get(Task, run.task_id)
        if not task:
            return
        account = db.get(DouyinAccount, task.account_id)
        if not account or account.status != "valid" or not account.state_ciphertext:
            run.status = "needs_account"
            run.error_code = "ACCOUNT_NOT_READY"
            run.error_message = "抖音账号尚未完成扫码绑定或登录已失效"
            run.finished_at = utcnow()
            notify_failure(db, run, failure_email_subject(db, run, "需要重新绑定"), failure_email_body(db, run, "自动任务未执行，因为抖音账号尚未绑定或登录已失效。"), "login_expired")
            db.commit()
            return
        task_recipient_by_name = {item.contact_name: item for item in task.recipients}
        recipients = []
        for item in run.items:
            if item.status != "pending":
                continue
            task_recipient = task_recipient_by_name.get(item.contact_name)
            contact = db.get(Contact, task_recipient.contact_id) if task_recipient and task_recipient.contact_id else None
            recipients.append((item.contact_name, item.message, contact.external_id if contact else None))
        try:
            state_json = decrypt_text(account.state_ciphertext)
        except Exception:
            account.status = "invalid"
            run.status = "needs_login"
            run.error_code = "STATE_DECRYPT_FAILED"
            run.error_message = "保存的登录状态无法解密，请重新扫码绑定"
            run.finished_at = utcnow()
            notify_failure(db, run, failure_email_subject(db, run, "需要重新绑定"), failure_email_body(db, run, "保存的抖音登录状态无法读取，请重新扫码绑定。"), "login_expired")
            db.commit()
            return

    def on_before_send(contact_name: str) -> None:
        with SessionLocal() as item_db:
            item = item_db.scalar(select(RunItem).where(RunItem.run_id == run_id, RunItem.contact_name == contact_name))
            if not item or item.status != "pending":
                raise RuntimeError(f"发送明细状态异常：{contact_name}")
            item.status = "sending"
            item.started_at = utcnow()
            item_db.commit()

    def on_result(result) -> None:
        with SessionLocal() as item_db:
            item = item_db.scalar(select(RunItem).where(RunItem.run_id == run_id, RunItem.contact_name == result.contact_name))
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
        result_map = {item.contact_name: item for item in results}
        for item in run.items:
            result = result_map.get(item.contact_name)
            if result:
                item.status = result.status
                item.reason = result.reason or None
                item.finished_at = item.finished_at or utcnow()
            elif fatal:
                if item.status == "sending":
                    item.status = "uncertain"
                    item.reason = "发送后未能保存确认结果；为避免重复未自动重试"
                elif item.status == "pending":
                    item.status = "skipped"
                    item.reason = "批次因账号或安全状态停止"
                item.finished_at = item.finished_at or utcnow()
        run.finished_at = utcnow()

        if fatal and fatal.startswith("transient:"):
            for item in run.items:
                if item.status == "pending":
                    item.status = "failed"
                    item.reason = fatal.split(":", 1)[1]
            uncertain = [item for item in run.items if item.status == "uncertain"]
            failed = [item for item in run.items if item.status == "failed"]
            if uncertain:
                run.status = "needs_review"
                run.error_code = "DELIVERY_UNCERTAIN"
                run.error_message = "服务异常时部分发送结果无法确认，为避免重复没有自动重试"
                notify_failure(db, run, failure_email_subject(db, run, "发送结果待确认"), failure_email_body(db, run, run.error_message), "task_failed")
            elif run.attempt < 3 and entitlement_is_active(db, run.user_id):
                _create_retry(db, run, failed)
            else:
                run.status = "failed"
                run.error_code = "RETRIES_EXHAUSTED"
                run.error_message = "浏览器执行连续失败 3 次"
                notify_failure(db, run, failure_email_subject(db, run, "连续失败 3 次"), failure_email_body(db, run, "自动执行连续尝试 3 次仍未完成，已停止重试。"), "task_failed")
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
            if uncertain:
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
        _settle_retry_ancestors(db, run)
        db.commit()


def main() -> None:
    Base.metadata.create_all(engine)
    logger.info("single-browser worker started")
    while True:
        try:
            schedule_due_tasks()
            process_daily_notifications()
            run_id = claim_one()
            if run_id:
                execute_run(run_id)
            elif not automation_due_soon():
                if not process_binding():
                    process_contact_sync()
        except Exception:
            logger.exception("worker loop failed")
        time.sleep(10)


if __name__ == "__main__":
    main()
