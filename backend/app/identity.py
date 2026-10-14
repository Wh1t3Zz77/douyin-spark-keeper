"""Account-scoped identity binding. Mutable display fields never identify a send."""
from collections import Counter
import re
from sqlalchemy import select
from .models import Contact, Task, TaskRecipient, RunItem


MISSING_ID = "缺少稳定好友标识，请重新同步好友并重新选择；不能仅凭昵称发送"


def unresolved_numeric_placeholder(raw):
    return bool(re.fullmatch(r"[0-9]{12,}", str(raw.get("display_name") or "").strip())
        and not any(raw.get(field) for field in ("external_id", "uid", "sec_uid", "conversation_id", "nickname", "remark_name")))


def recent_contacts(db, account_id, user_id, sync_started_at=None):
    """Show the latest observed list plus saved selections/plan bindings.

    Keep historical rows in storage for bindings and audit, not in the recent list.
    """
    contacts = db.scalars(select(Contact).where(Contact.account_id == account_id,
        Contact.user_id == user_id).order_by(Contact.conversation_type, Contact.display_name)).all()
    tasks = db.scalars(select(Task).where(Task.account_id == account_id,
        Task.user_id == user_id, Task.deleted_at.is_(None))).all()
    bound_ids = {r.contact_id for t in tasks for r in t.recipients if r.contact_id}
    legacy_names = {r.contact_name for t in tasks for r in t.recipients if not r.contact_id}
    visible = []
    for contact in contacts:
        retained = contact.selected or contact.id in bound_ids or contact.display_name in legacy_names
        placeholder = unresolved_numeric_placeholder({field: getattr(contact, field)
            for field in ("display_name", "external_id", "uid", "sec_uid", "conversation_id", "nickname", "remark_name")})
        if retained or (not placeholder and (sync_started_at is None or
                contact.last_seen_at and contact.last_seen_at >= sync_started_at)):
            visible.append(contact)
    return visible


def stored_identity_matches(contact, identity):
    if not identity:
        return False
    if contact.external_id == identity:
        return True
    for prefix, field in (("uid:", "uid"), ("sec:", "sec_uid"), ("conv:", "conversation_id")):
        if identity.startswith(prefix):
            return identity[len(prefix):] == str(getattr(contact, field) or "")
    return identity in {str(getattr(contact, field) or "") for field in ("uid", "sec_uid", "conversation_id")}


def contact_for_recipient(db, task, recipient):
    if recipient.contact_id:
        contact = db.get(Contact, recipient.contact_id)
        return contact if contact and contact.account_id == task.account_id and contact.user_id == task.user_id else None
    matches = db.scalars(select(Contact).where(
        Contact.account_id == task.account_id, Contact.user_id == task.user_id,
        Contact.display_name == recipient.contact_name,
    )).all()
    # A unique saved row is insufficient when synchronization saw duplicate names.
    if len(matches) != 1 or not matches[0].external_id or matches[0].identity_conflict:
        return None
    recipient.contact_id = matches[0].id
    return matches[0]


def snapshot_item(db, task, recipient):
    contact = contact_for_recipient(db, task, recipient)
    return RunItem(recipient_id=recipient.id, contact_id=contact.id if contact else None,
                   external_id=contact.external_id if contact else None,
                   contact_name=contact.display_name if contact else recipient.contact_name,
                   message=recipient.message)


def resolve_run_contact(db, task, item):
    if item.contact_id:
        contact = db.get(Contact, item.contact_id)
        if not contact or contact.account_id != task.account_id or contact.user_id != task.user_id:
            return None
        # A queued/retry item may never silently change to another identity.
        if item.external_id and not stored_identity_matches(contact, item.external_id):
            return None
    else:
        candidates = [r for r in task.recipients if
                      (r.id == item.recipient_id if item.recipient_id else r.contact_name == item.contact_name)]
        if len(candidates) != 1:
            return None
        contact = contact_for_recipient(db, task, candidates[0])
        if not contact:
            return None
        item.recipient_id = candidates[0].id
        item.contact_id = contact.id
    if not contact.external_id or contact.identity_conflict:
        return None
    item.external_id = contact.external_id
    item.contact_name = contact.display_name
    return contact


def backfill_legacy_bindings(db):
    for task in db.scalars(select(Task).where(Task.deleted_at.is_(None))).all():
        for recipient in task.recipients:
            contact = contact_for_recipient(db, task, recipient)
            if contact and contact.external_id and not contact.identity_conflict:
                recipient.contact_name = contact.display_name
                recipient.conversation_type = contact.conversation_type


def sync_contacts(db, account_id, user_id, raw_contacts):
    rows = [r for r in raw_contacts if isinstance(r, dict) and str(r.get("display_name") or "").strip()
        and not unresolved_numeric_placeholder(r)]
    counts = Counter(str(r["display_name"]).strip() for r in rows)
    existing = db.scalars(select(Contact).where(Contact.account_id == account_id, Contact.user_id == user_id)).all()
    for raw in rows:
        name = str(raw["display_name"]).strip()[:120]
        external = str(raw.get("external_id") or "")[:240] or None
        source = external or f"name:{name}"
        matches = [c for c in existing if external and c.external_id == external]
        # Old untyped identifiers can be upgraded only when an exact stable value agrees.
        if not matches and external:
            values = {str(raw.get(k) or "") for k in ("uid", "sec_uid", "conversation_id")}
            matches = [c for c in existing if c.external_id and not c.external_id.startswith(("uid:", "sec:", "conv:")) and c.external_id in values]
        if not matches and external:
            # A fallback uid/sec_uid may later gain conversation metadata. Keep the same object.
            def same_user(c):
                if c.conversation_type == "group" or raw.get("conversation_type") == "group":
                    return False
                shared = any(raw.get(field) and (str(getattr(c, field) or "") == str(raw[field])
                    or c.external_id == prefix + str(raw[field]))
                    for prefix, field in (("uid:", "uid"), ("sec:", "sec_uid")))
                conflict = any(getattr(c, field) and raw.get(field) and str(getattr(c, field)) != str(raw[field])
                    for field in ("uid", "sec_uid"))
                return shared and not conflict
            matches = [c for c in existing if same_user(c)]
        if not matches and counts[name] == 1:
            matches = [c for c in existing if c.display_name == name and not c.external_id and not c.identity_conflict]
        if len(matches) > 1:
            for c in matches:
                c.identity_conflict = True
            continue
        contact = matches[0] if matches else None
        if not contact:
            # Never overwrite a previously ambiguous name-only row.
            same_source = [c for c in existing if c.source_key == source]
            if same_source:
                same_source[0].identity_conflict = True
                continue
            contact = Contact(account_id=account_id, user_id=user_id, source_key=source, display_name=name)
            db.add(contact)
            existing.append(contact)
        contact.source_key = source
        contact.display_name = name
        contact.external_id = external or contact.external_id
        contact.identity_conflict = counts[name] > 1 and not external
        for field in ("uid", "sec_uid", "conversation_id", "douyin_id", "nickname", "remark_name"):
            value = str(raw.get(field) or "")[:240] or None
            if value or field in raw.get("_profile_fields", []):
                setattr(contact, field, value)
        if not contact.type_locked:
            contact.conversation_type = raw.get("conversation_type") if raw.get("conversation_type") in {"friend", "group"} else "unknown"
        contact.avatar_url = raw.get("avatar_url")
        contact.streak_days = raw.get("streak_days")
        from .models import utcnow
        contact.last_seen_at = utcnow()
        db.flush()
    tasks = db.scalars(select(Task).where(Task.account_id == account_id, Task.user_id == user_id, Task.deleted_at.is_(None))).all()
    for task in tasks:
        for recipient in task.recipients:
            contact = contact_for_recipient(db, task, recipient)
            if contact and contact.external_id and not contact.identity_conflict:
                recipient.contact_name = contact.display_name
                recipient.conversation_type = contact.conversation_type
                contact.selected = True
                contact.planned_message = recipient.message
    return existing
