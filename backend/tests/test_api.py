import os
import tempfile
from datetime import datetime, timedelta


db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
db_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{db_file.name.replace(os.sep, '/')}"
os.environ["APP_SECRET"] = "test-secret-that-is-long-enough-for-tests"
os.environ["BOOTSTRAP_INVITE"] = "TEST-INVITE"
os.environ["BOOTSTRAP_INVITE_USES"] = "30"
os.environ["DEBUG_RETURN_CODES"] = "true"
os.environ["APP_ENV"] = "test"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["SMTP_HOST"] = "smtp.example.com"
os.environ["SMTP_FROM"] = "noreply@example.com"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

import app.main as main_module
from app.main import app
from app.database import SessionLocal
from app.models import BindingSession, Contact, ContactSync, DouyinAccount, EmailOutbox, Run, RunItem, Task, User, UserEntitlement, utcnow
import app.worker as worker_module
from app.worker import claim_one, schedule_due_tasks


def register(client: TestClient, username: str, email: str) -> None:
    started = client.post(
        "/api/auth/register/start",
        json={
            "username": username,
            "email": email,
            "password": "Zlh12345",
            "confirm_password": "Zlh12345",
            "invite_code": "TEST-INVITE",
        },
    )
    assert started.status_code == 202, started.text
    code = started.json()["verification_code"]
    with SessionLocal() as db:
        email_item = db.scalars(
            select(EmailOutbox).where(EmailOutbox.recipient == email).order_by(EmailOutbox.created_at.desc())
        ).first()
        assert email_item is not None
        assert email_item.body.startswith("请勿转发给他人。你的验证码是：")
        assert f"{code}，10 分钟内有效。" in email_item.body
    verified = client.post("/api/auth/register/verify", json={"email": email, "code": code})
    assert verified.status_code == 201, verified.text


def test_private_selfhosted_registration_works_without_smtp(monkeypatch):
    monkeypatch.setattr(main_module.settings, "email_verification_required", False)
    monkeypatch.setattr(main_module.settings, "smtp_host", "")
    monkeypatch.setattr(main_module.settings, "smtp_from", "")

    with TestClient(app) as client:
        started = client.post(
            "/api/auth/register/start",
            json={
                "username": "local_owner",
                "email": "local-owner@example.com",
                "password": "Localpass123",
                "confirm_password": "Localpass123",
                "invite_code": "TEST-INVITE",
            },
        )
        assert started.status_code == 202, started.text
        code = started.json()["verification_code"]
        with SessionLocal() as db:
            assert db.scalar(select(EmailOutbox).where(EmailOutbox.recipient == "local-owner@example.com")) is None
        verified = client.post(
            "/api/auth/register/verify",
            json={"email": "local-owner@example.com", "code": code},
        )
        assert verified.status_code == 201, verified.text


def test_registration_login_and_tenant_isolation():
    with TestClient(app) as first:
        register(first, "first_user", "first@example.com")
        me = first.get("/api/me")
        assert me.status_code == 200
        assert me.json()["username"] == "first_user"

        account = first.post("/api/accounts", json={"name": "账号一"})
        assert account.status_code == 201
        account_id = account.json()["id"]

        task = first.post(
            "/api/tasks",
            json={
                "account_id": account_id,
                "name": "晚间续火",
                "send_time": "20:10",
                "recipients": [{"contact_name": "好友A", "message": "今日火花"}],
            },
        )
        assert task.status_code == 201, task.text
        assert task.json()["recipients"][0]["contact_name"] == "好友A"

        updated = first.put(
            f"/api/tasks/{task.json()['id']}",
            json={
                "account_id": account_id,
                "name": "晚间续火",
                "send_time": "20:20",
                "recipients": [{"contact_name": "好友A", "message": "你好"}],
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["send_time"] == "20:20"

        disabled = first.patch(f"/api/tasks/{task.json()['id']}", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False

        not_bound = first.post(f"/api/tasks/{task.json()['id']}/runs")
        assert not_bound.status_code == 403
        assert not_bound.json()["detail"] == "普通用户的任务只按已保存时间自动执行"

        queue = first.get("/api/queue")
        assert queue.status_code == 200
        assert queue.json()["position"] is None

        email = first.post("/api/settings/notifications/test")
        assert email.status_code == 202

        with TestClient(app) as second:
            register(second, "second_user", "second@example.com")
            assert second.get("/api/accounts").json() == []
            assert second.get("/api/tasks").json() == []
            forbidden = second.post(
                "/api/tasks",
                json={
                    "account_id": account_id,
                    "name": "越权任务",
                    "send_time": "21:10",
                    "recipients": [{"contact_name": "好友B", "message": "今日火花"}],
                },
            )
            assert forbidden.status_code == 404


def test_password_policy_and_invalid_email():
    with TestClient(app) as client:
        lower_only = client.post(
            "/api/auth/register/start",
            json={
                "username": "third_user",
                "email": "third@example.com",
                "password": "zlh700816",
                "confirm_password": "zlh700816",
                "invite_code": "TEST-INVITE",
            },
        )
        assert lower_only.status_code == 422

        invalid_email = client.post(
            "/api/auth/register/start",
            json={
                "username": "third_user",
                "email": "1",
                "password": "Zlh700816",
                "confirm_password": "Zlh700816",
                "invite_code": "TEST-INVITE",
            },
        )
        assert invalid_email.status_code == 422


def test_password_reset_and_unknown_email_privacy():
    with TestClient(app) as client:
        register(client, "reset_user", "reset@example.com")
        unknown = client.post("/api/auth/password/reset/start", json={"email": "missing@example.com"})
        assert unknown.status_code == 202
        assert "verification_code" not in unknown.json()

        started = client.post("/api/auth/password/reset/start", json={"email": "reset@example.com"})
        assert started.status_code == 202, started.text
        code = started.json()["verification_code"]
        wrong = client.post(
            "/api/auth/password/reset/verify",
            json={"email": "reset@example.com", "code": "000000", "password": "Newpass123", "confirm_password": "Newpass123"},
        )
        assert wrong.status_code == 400
        verified = client.post(
            "/api/auth/password/reset/verify",
            json={"email": "reset@example.com", "code": code, "password": "Newpass123", "confirm_password": "Newpass123"},
        )
        assert verified.status_code == 200, verified.text
        assert client.post("/api/auth/login", json={"username": "reset_user", "password": "Zlh12345"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "reset_user", "password": "Newpass123"}).status_code == 200


def test_signed_in_user_can_change_password():
    with TestClient(app) as client:
        register(client, "change_user", "change@example.com")
        wrong = client.post(
            "/api/auth/password/change",
            json={"current_password": "Wrong123", "password": "Changed123", "confirm_password": "Changed123"},
        )
        assert wrong.status_code == 400
        changed = client.post(
            "/api/auth/password/change",
            json={"current_password": "Zlh12345", "password": "Changed123", "confirm_password": "Changed123"},
        )
        assert changed.status_code == 200, changed.text
        assert client.get("/api/me").status_code == 200
        assert client.post("/api/auth/login", json={"username": "change_user", "password": "Zlh12345"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "change_user", "password": "Changed123"}).status_code == 200


def test_free_access_expires_seven_days_after_registration():
    with TestClient(app) as client:
        register(client, "expired_user", "expired@example.com")
        account = client.post("/api/accounts", json={"name": "到期账号"}).json()
        existing_task = client.post(
            "/api/tasks",
            json={"account_id": account["id"], "name": "到期前计划", "send_time": "07:28", "recipients": [{"contact_name": "好友A", "message": "今日火花"}]},
        )
        assert existing_task.status_code == 201, existing_task.text
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == "expired@example.com").one()
            entitlement = db.get(UserEntitlement, user.id)
            assert entitlement is not None
            assert timedelta(days=6, hours=23) < entitlement.expires_at - user.created_at < timedelta(days=7, minutes=1)
            entitlement.expires_at = utcnow() - timedelta(seconds=1)
            task = db.get(Task, existing_task.json()["id"])
            task.next_run_at = utcnow() - timedelta(minutes=1)
            db.commit()

        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["plan_tier"] == "expired"
        assert me.json()["limits"]["tasks_per_account"] == 0
        blocked = client.post(
            "/api/tasks",
            json={"account_id": account["id"], "name": "过期计划", "send_time": "07:29", "recipients": [{"contact_name": "好友A", "message": "今日火花"}]},
        )
        assert blocked.status_code == 403
        assert "套餐已到期" in blocked.json()["detail"]
        schedule_due_tasks()
        with SessionLocal() as db:
            assert db.query(Run).filter(Run.task_id == existing_task.json()["id"]).count() == 0


def test_expired_free_user_has_already_queued_run_cancelled_before_execution():
    with TestClient(app) as client:
        register(client, "expired_queue", "expired-queue@example.com")
        account = client.post("/api/accounts", json={"name": "排队账号"}).json()
        task = client.post(
            "/api/tasks",
            json={"account_id": account["id"], "name": "到期边界计划", "send_time": "07:28", "recipients": [{"contact_name": "好友A", "message": "今日火花"}]},
        ).json()
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == "expired-queue@example.com").one()
            entitlement = db.get(UserEntitlement, user.id)
            queued = Run(task_id=task["id"], user_id=user.id, scheduled_for=utcnow() - timedelta(seconds=1))
            queued.items = [RunItem(contact_name="好友A", message="今日火花")]
            db.add(queued)
            entitlement.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
            run_id = queued.id

        assert claim_one() is None
        with SessionLocal() as db:
            cancelled = db.get(Run, run_id)
            assert cancelled.status == "cancelled"
            assert cancelled.error_code == "PLAN_EXPIRED"
            assert cancelled.items[0].status == "skipped"


def test_pro_limits_and_expiry_are_returned_by_me():
    with TestClient(app) as client:
        register(client, "pro_limits", "pro-limits@example.com")
        user_id = client.get("/api/me").json()["id"]
        with SessionLocal() as db:
            entitlement = db.get(UserEntitlement, user_id)
            entitlement.plan_tier = "pro"
            entitlement.expires_at = utcnow() + timedelta(days=365)
            db.commit()

        me = client.get("/api/me").json()
        assert me["plan_tier"] == "pro"
        assert me["limits"]["tasks_total"] == 10
        assert me["limits"]["recipients_total"] == 25
        assert me["plan_started_at"] is not None
        assert me["plan_expires_at"] is not None

        account = client.post("/api/accounts", json={"name": "Pro 到期账号"}).json()
        task = client.post(
            "/api/tasks",
            json={"account_id": account["id"], "name": "Pro 到期计划", "send_time": "07:28", "recipients": [{"contact_name": "好友A", "message": "今日火花"}]},
        ).json()

        with SessionLocal() as db:
            entitlement = db.get(UserEntitlement, user_id)
            entitlement.expires_at = utcnow() - timedelta(seconds=1)
            db.get(Task, task["id"]).next_run_at = utcnow() - timedelta(minutes=1)
            db.commit()
        assert client.get("/api/me").json()["plan_tier"] == "expired"
        schedule_due_tasks()
        with SessionLocal() as db:
            assert db.query(Run).filter(Run.task_id == task["id"]).count() == 0


def test_only_one_binding_session_can_be_active():
    with TestClient(app) as first:
        register(first, "bind_user_one", "bind-one@example.com")
        first_account = first.post("/api/accounts", json={"name": "绑定账号一"}).json()
        started = first.post(f"/api/accounts/{first_account['id']}/binding")
        assert started.status_code == 202, started.text
        binding_id = started.json()["id"]
        own_status = first.get(f"/api/accounts/{first_account['id']}/binding/{binding_id}")
        assert own_status.status_code == 200
        assert own_status.json()["status"] == "pending"
        busy_delete = first.delete(f"/api/accounts/{first_account['id']}")
        assert busy_delete.status_code == 409
        assert "正在进行" in busy_delete.json()["detail"]

        with TestClient(app) as second:
            register(second, "bind_user_two", "bind-two@example.com")
            second_account = second.post("/api/accounts", json={"name": "绑定账号二"}).json()
            blocked = second.post(f"/api/accounts/{second_account['id']}/binding")
            assert blocked.status_code == 409
            assert second.post(f"/api/accounts/{first_account['id']}/binding/{binding_id}/cancel").status_code == 404

            cancelled = first.post(f"/api/accounts/{first_account['id']}/binding/{binding_id}/cancel")
            assert cancelled.status_code == 200
            expired = first.get(f"/api/accounts/{first_account['id']}/binding/{binding_id}")
            assert expired.json()["status"] == "expired"
            assert expired.json()["qr_data_url"] is None
            restarted = second.post(f"/api/accounts/{second_account['id']}/binding")
            assert restarted.status_code == 202


def test_account_limit_is_five_per_user():
    with TestClient(app) as client:
        register(client, "multi_account_user", "multi@example.com")
        for number in range(5):
            created = client.post("/api/accounts", json={"name": f"抖音账号 {number + 1}"})
            assert created.status_code == 201
        rejected = client.post("/api/accounts", json={"name": "第六个账号"})
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "每位用户最多绑定 5 个抖音账号"

        accounts = client.get("/api/accounts").json()
        created_plans = []
        for number, send_time in enumerate(["03:17", "04:17", "05:17", "06:17", "08:17"]):
            created = client.post(
                "/api/tasks",
                json={
                    "account_id": accounts[number % 2]["id"],
                    "name": f"免费计划 {number + 1}",
                    "send_time": send_time,
                    "recipients": [
                        {"contact_name": f"好友{number * 2 + 1}", "message": "今日火花"},
                        {"contact_name": f"好友{number * 2 + 2}", "message": "今日火花"},
                    ],
                },
            )
            assert created.status_code == 201, created.text
            created_plans.append(created.json())

        me = client.get("/api/me").json()
        assert me["limits"]["tasks_total"] == 5
        assert me["limits"]["recipients_total"] == 10
        sixth_plan = client.post(
            "/api/tasks",
            json={"account_id": accounts[2]["id"], "name": "超额计划", "send_time": "09:17", "recipients": [{"contact_name": "好友11", "message": "今日火花"}]},
        )
        assert sixth_plan.status_code == 409
        assert "合计最多创建 5 个计划" in sixth_plan.json()["detail"]

        over_recipient_limit = client.put(
            f"/api/tasks/{created_plans[0]['id']}",
            json={
                "account_id": created_plans[0]["account_id"],
                "name": created_plans[0]["name"],
                "send_time": created_plans[0]["send_time"],
                "recipients": [
                    {"contact_name": "好友1", "message": "今日火花"},
                    {"contact_name": "好友2", "message": "今日火花"},
                    {"contact_name": "好友11", "message": "今日火花"},
                ],
            },
        )
        assert over_recipient_limit.status_code == 409
        assert "合计最多安排 10 人" in over_recipient_limit.json()["detail"]

        availability = client.get("/api/schedule/availability", params={"send_time": "03:17"})
        assert availability.status_code == 200
        assert availability.json()["nearby_tasks"] >= 1


def test_account_delete_cascades_and_is_tenant_scoped():
    with TestClient(app) as owner:
        register(owner, "delete_owner", "delete-owner@example.com")
        account = owner.post("/api/accounts", json={"name": "准备删除的账号"}).json()
        task = owner.post(
            "/api/tasks",
            json={
                "account_id": account["id"],
                "name": "随账号删除的计划",
                "send_time": "06:31",
                "recipients": [{"contact_name": "好友A", "message": "今日火花"}],
            },
        )
        assert task.status_code == 201, task.text

        with TestClient(app) as stranger:
            register(stranger, "delete_stranger", "delete-stranger@example.com")
            assert stranger.delete(f"/api/accounts/{account['id']}").status_code == 404

        deleted = owner.delete(f"/api/accounts/{account['id']}")
        assert deleted.status_code == 204, deleted.text
        assert owner.get("/api/accounts").json() == []
        assert owner.get("/api/tasks").json() == []


def test_admin_can_manage_invites_and_regular_users_cannot():
    with TestClient(app) as admin:
        register(admin, "admin_user", "admin@example.com")
        with SessionLocal() as db:
            for stale in db.scalars(select(BindingSession).where(BindingSession.status.in_(["pending", "running", "qr_ready", "verification_required", "verification_submitted"]))).all():
                stale.status = "expired"
            db.commit()
        account = admin.post("/api/accounts", json={"name": "管理员测试账号"}).json()
        plan = admin.post(
            "/api/tasks",
            json={
                "account_id": account["id"],
                "name": "管理员晚间计划",
                "send_time": "20:15",
                "recipients": [{"contact_name": "好友甲", "message": "今日火花"}],
            },
        )
        assert plan.status_code == 201, plan.text
        started_binding = admin.post(f"/api/accounts/{account['id']}/binding")
        assert started_binding.status_code == 202
        binding_id = started_binding.json()["id"]
        with SessionLocal() as db:
            binding = db.get(BindingSession, binding_id)
            binding.status = "verification_required"
            db.commit()
        submitted = admin.post(f"/api/accounts/{account['id']}/binding/{binding_id}/verification", json={"code": "123456"})
        assert submitted.status_code == 202
        with SessionLocal() as db:
            binding = db.get(BindingSession, binding_id)
            assert binding.status == "verification_submitted"
            assert binding.verification_code_ciphertext != "123456"

        users = admin.get("/api/admin/users")
        assert users.status_code == 200
        admin_record = next(item for item in users.json() if item["email"] == "admin@example.com")
        assert admin_record["accounts"][0]["latest_binding"]["status"] == "verification_submitted"
        assert admin_record["accounts"][0]["latest_binding"]["error_message"] is None
        assert admin_record["accounts"][0]["plans"] == [{
            "id": plan.json()["id"],
            "name": "管理员晚间计划",
            "send_time": "20:15",
            "enabled": True,
            "recipients": ["好友甲"],
        }]
        with TestClient(app) as upgrade_member:
            register(upgrade_member, "upgrade_member", "upgrade-member@example.com")
            member_id = upgrade_member.get("/api/me").json()["id"]
            upgraded = admin.post(f"/api/admin/users/{member_id}/upgrade-pro", json={"period": "year"})
            assert upgraded.status_code == 200, upgraded.text
            assert upgraded.json()["plan_tier"] == "pro"
            assert upgraded.json()["plan_period"] == "year"
            assert upgraded.json()["plan_remaining_days"] == 365
            assert upgrade_member.get("/api/me").json()["plan_tier"] == "pro"
            assert upgrade_member.post(f"/api/admin/users/{member_id}/upgrade-pro").status_code == 403
            assert admin.post(f"/api/admin/users/{member_id}/upgrade-pro").status_code == 409
            assert admin.post(f"/api/admin/users/{admin_record['id']}/upgrade-pro").status_code == 409
            with SessionLocal() as db:
                entitlement = db.get(UserEntitlement, member_id)
                entitlement.expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
            expired_pro = upgrade_member.get("/api/me").json()
            assert expired_pro["plan_tier"] == "expired"
            assert expired_pro["plan_period"] == "year"
        cancelled = admin.post(f"/api/admin/bindings/{binding_id}/cancel")
        assert cancelled.status_code == 200

        created = admin.post("/api/admin/invites", json={"max_uses": 2, "expires_in_days": 7})
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["code"].startswith("SPARK-")
        assert payload["max_uses"] == 2

        listed = admin.get("/api/admin/invites")
        assert listed.status_code == 200
        assert any(item["id"] == payload["id"] for item in listed.json())

        disabled = admin.patch(f"/api/admin/invites/{payload['id']}", json={"active": False})
        assert disabled.status_code == 200
        assert disabled.json()["active"] is False

        deleted = admin.delete(f"/api/admin/invites/{payload['id']}")
        assert deleted.status_code == 204

    with TestClient(app) as member:
        register(member, "regular_user", "regular@example.com")
        assert member.get("/api/admin/invites").status_code == 403
        assert member.get("/api/admin/users").status_code == 403


def test_schedule_recommendation_and_runs_are_grouped_by_beijing_date():
    with TestClient(app) as client:
        register(client, "dated_runs", "dated-runs@example.com")
        recommendation = client.get("/api/schedule/recommendation")
        assert recommendation.status_code == 200, recommendation.text
        send_hour, send_minute = map(int, recommendation.json()["send_time"].split(":"))
        assert (send_hour, send_minute) >= (18, 30)
        assert (send_hour, send_minute) <= (22, 30)
        assert send_minute % 5 == 0

        account = client.post("/api/accounts", json={"name": "日期分页账号"}).json()
        task = client.post(
            "/api/tasks",
            json={
                "account_id": account["id"],
                "name": "日期分页计划",
                "send_time": "20:05",
                "recipients": [{"contact_name": "好友A", "message": "不应出现在邮件中"}],
            },
        ).json()
        user_id = client.get("/api/me").json()["id"]
        with SessionLocal() as db:
            first = Run(task_id=task["id"], user_id=user_id, scheduled_for=datetime(2026, 8, 1, 15, 30), status="failed")
            first.items = [RunItem(contact_name="好友A", message="不应出现在邮件中", status="failed", reason="找不到会话")]
            second = Run(task_id=task["id"], user_id=user_id, scheduled_for=datetime(2026, 8, 1, 16, 30), status="success")
            second.items = [RunItem(contact_name="好友A", message="另一个消息正文", status="submitted")]
            db.add_all([first, second])
            db.commit()
            first_id, second_id = first.id, second.id
            body = worker_module.failure_email_body(db, first, "连续尝试仍未完成。")

        assert client.get("/api/runs/dates").json()[:2] == ["2026-08-02", "2026-08-01"]
        day_one = client.get("/api/runs", params={"date": "2026-08-01"})
        day_two = client.get("/api/runs", params={"date": "2026-08-02"})
        assert [item["id"] for item in day_one.json()] == [first_id]
        assert [item["id"] for item in day_two.json()] == [second_id]
        assert "日期分页账号" in body
        assert "日期分页计划" in body
        assert "好友A：找不到会话" in body
        assert "不应出现在邮件中" not in body


def test_admin_can_page_all_account_runs_by_beijing_date():
    with TestClient(app) as member, TestClient(app) as admin:
        register(member, "history_member", "history-member@example.com")
        register(admin, "history_admin", "history-admin@example.com")
        member_id = member.get("/api/me").json()["id"]
        admin_id = admin.get("/api/me").json()["id"]
        account_id = member.post("/api/accounts", json={"name": "历史账号"}).json()["id"]
        other_account_id = member.post("/api/accounts", json={"name": "其他账号"}).json()["id"]
        task_id = member.post("/api/tasks", json={
            "account_id": account_id, "name": "历史计划", "send_time": "20:00",
            "recipients": [{"contact_name": "好友A", "message": "测试"}],
        }).json()["id"]
        other_task_id = member.post("/api/tasks", json={
            "account_id": other_account_id, "name": "其他计划", "send_time": "21:00",
            "recipients": [{"contact_name": "好友B", "message": "测试"}],
        }).json()["id"]
        with SessionLocal() as db:
            db.get(User, admin_id).is_admin = True
            records = [Run(task_id=task_id, user_id=member_id, scheduled_for=datetime(2026, 8, 1, 15, minute), status="success") for minute in range(7)]
            for record in records:
                record.items = [RunItem(contact_name="好友A", message="不展示消息正文", status="submitted")]
            next_day = Run(task_id=task_id, user_id=member_id, scheduled_for=datetime(2026, 8, 1, 16, 1), status="failed", error_message="测试失败")
            next_day.items = [RunItem(contact_name="好友A", message="不展示消息正文", status="failed", reason="好友不可用")]
            other = Run(task_id=other_task_id, user_id=member_id, scheduled_for=datetime(2026, 8, 1, 15, 0), status="success")
            db.add_all([*records, next_day, other])
            db.commit()

        assert member.get(f"/api/admin/accounts/{account_id}/runs/dates").status_code == 403
        assert member.get(f"/api/admin/accounts/{account_id}/runs", params={"date": "2026-08-01"}).status_code == 403
        assert admin.get(f"/api/admin/accounts/{account_id}/runs/dates").json() == ["2026-08-02", "2026-08-01"]
        first_day = admin.get(f"/api/admin/accounts/{account_id}/runs", params={"date": "2026-08-01"})
        assert first_day.status_code == 200
        assert len(first_day.json()) == 7
        assert all(item["task_name"] == "历史计划" for item in first_day.json())
        second_day = admin.get(f"/api/admin/accounts/{account_id}/runs", params={"date": "2026-08-02"})
        assert len(second_day.json()) == 1
        assert second_day.json()[0]["items"][0]["reason"] == "好友不可用"
        assert admin.get(f"/api/admin/accounts/{account_id}/runs", params={"date": "2026-02-30"}).status_code == 422
        assert admin.get("/api/admin/accounts/missing/runs/dates").status_code == 404


def test_contacts_persist_selection_syncs_task_and_task_can_be_deleted():
    with TestClient(app) as client:
        register(client, "contact_user", "contacts@example.com")
        user_id = client.get("/api/me").json()["id"]
        account_id = client.post("/api/accounts", json={"name": "联系人测试号"}).json()["id"]
        with SessionLocal() as db:
            friend = Contact(account_id=account_id, user_id=user_id, source_key="friend:小明", display_name="小明", conversation_type="friend", streak_days=18)
            group = Contact(account_id=account_id, user_id=user_id, source_key="group:家人群", display_name="家人群", conversation_type="group")
            unknown = Contact(account_id=account_id, user_id=user_id, source_key="unknown:未识别", display_name="未识别", conversation_type="unknown")
            db.add_all([friend, group, unknown])
            db.commit()
            friend_id, group_id = friend.id, group.id

        listed = client.get(f"/api/accounts/{account_id}/contacts")
        assert listed.status_code == 200
        assert {item["conversation_type"] for item in listed.json()["contacts"]} == {"friend", "group", "unknown"}

        saved = client.put(
            f"/api/accounts/{account_id}/contacts/selection",
            json={"recipients": [{"contact_id": friend_id, "contact_name": "ignored", "conversation_type": "unknown", "message": "朋友消息"}]},
        )
        assert saved.status_code == 200
        assert saved.json()["task_updated"] is False
        assert [item["display_name"] for item in saved.json()["contacts"] if item["selected"]] == ["小明"]

        created = client.post(
            "/api/tasks",
            json={
                "account_id": account_id,
                "name": "每日续火",
                "send_time": "06:41",
                "enabled": False,
                "recipients": [{"contact_id": friend_id, "contact_name": "ignored", "conversation_type": "unknown", "message": "朋友消息"}],
            },
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]
        assert created.json()["enabled"] is False
        assert created.json()["recipients"][0]["conversation_type"] == "friend"

        updated = client.put(
            f"/api/accounts/{account_id}/contacts/selection",
            json={"recipients": [{"contact_id": group_id, "contact_name": "ignored", "conversation_type": "friend", "message": "群消息"}]},
        )
        assert updated.status_code == 200
        assert updated.json()["task_updated"] is False
        task = next(item for item in client.get("/api/tasks").json() if item["id"] == task_id)
        assert task["enabled"] is False
        assert task["recipients"][0]["contact_name"] == "小明"
        assert task["recipients"][0]["conversation_type"] == "friend"

        with SessionLocal() as db:
            run = Run(task_id=task_id, user_id=user_id, scheduled_for=utcnow(), status="success", attempt=1)
            run.items = [RunItem(contact_name="小明", message="朋友消息", status="submitted")]
            db.add(run)
            db.commit()
            run_id = run.id

        deleted = client.delete(f"/api/tasks/{task_id}")
        assert deleted.status_code == 204
        assert client.get("/api/tasks").json() == []
        preserved_run = next(item for item in client.get("/api/runs").json() if item["id"] == run_id)
        assert preserved_run["task_name"] == "每日续火"
        assert preserved_run["account_id"] == account_id
        assert preserved_run["items"][0]["status"] == "submitted"
        assert client.get(f"/api/accounts/{account_id}/contacts").json()["contacts"]


def test_admin_can_create_multiple_contact_plans_without_duplicate_targets():
    with TestClient(app) as client:
        register(client, "multi_plan_admin", "multi-plan@example.com")
        user_id = client.get("/api/me").json()["id"]
        account_id = client.post("/api/accounts", json={"name": "多计划账号"}).json()["id"]
        with SessionLocal() as db:
            user = db.get(User, user_id)
            user.is_admin = True
            first = Contact(account_id=account_id, user_id=user_id, source_key="friend:first", display_name="好友一", conversation_type="friend")
            second = Contact(account_id=account_id, user_id=user_id, source_key="friend:second", display_name="好友二", conversation_type="friend")
            db.add_all([first, second])
            db.commit()
            first_id, second_id = first.id, second.id

        admin_me = client.get("/api/me").json()
        assert admin_me["limits"]["tasks_per_account"] == 5
        assert admin_me["plan_tier"] == "pro"
        assert admin_me["plan_started_at"] is None
        assert admin_me["plan_expires_at"] is None
        first_task = client.post(
            "/api/tasks",
            json={"account_id": account_id, "name": "计划一", "send_time": "01:37", "recipients": [{"contact_id": first_id, "contact_name": "忽略", "message": "火花一"}]},
        )
        assert first_task.status_code == 201, first_task.text
        second_task = client.post(
            "/api/tasks",
            json={"account_id": account_id, "name": "计划二", "send_time": "01:57", "recipients": [{"contact_id": second_id, "contact_name": "忽略", "message": "火花二"}]},
        )
        assert second_task.status_code == 201, second_task.text
        duplicate = client.put(
            f"/api/tasks/{second_task.json()['id']}",
            json={"account_id": account_id, "name": "计划二", "send_time": "01:57", "recipients": [{"contact_id": first_id, "contact_name": "忽略", "message": "重复"}]},
        )
        assert duplicate.status_code == 409

        changed = client.patch(
            f"/api/accounts/{account_id}/contacts/{second_id}",
            json={"conversation_type": "group"},
        )
        assert changed.status_code == 200
        assert changed.json()["conversation_type"] == "group"
        assert changed.json()["type_locked"] is True
        task = next(item for item in client.get("/api/tasks").json() if item["id"] == second_task.json()["id"])
        assert task["recipients"][0]["conversation_type"] == "group"


def test_v22_migration_adds_recipient_identity_columns(tmp_path, monkeypatch):
    old_db = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with old_db.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE task_recipients ("
            "id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36), contact_name VARCHAR(120), "
            "message VARCHAR(500), enabled BOOLEAN DEFAULT 1 NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE contacts (id VARCHAR(36) PRIMARY KEY, conversation_type VARCHAR(16) DEFAULT 'unknown' NOT NULL)"
        )
        connection.exec_driver_sql("CREATE TABLE runs (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE run_items (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE email_outbox (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE user_entitlements (user_id VARCHAR(36) PRIMARY KEY, plan_tier VARCHAR(24))")
    monkeypatch.setattr(main_module, "engine", old_db)
    main_module._migrate_schema()
    columns = {column["name"] for column in inspect(old_db).get_columns("task_recipients")}
    assert {"contact_id", "conversation_type"}.issubset(columns)
    contact_columns = {column["name"] for column in inspect(old_db).get_columns("contacts")}
    assert "type_locked" in contact_columns
    run_columns = {column["name"] for column in inspect(old_db).get_columns("runs")}
    assert "parent_run_id" in run_columns
    item_columns = {column["name"] for column in inspect(old_db).get_columns("run_items")}
    assert {"started_at", "finished_at"}.issubset(item_columns)
    outbox_columns = {column["name"] for column in inspect(old_db).get_columns("email_outbox")}
    assert "dedup_key" in outbox_columns
    entitlement_columns = {column["name"] for column in inspect(old_db).get_columns("user_entitlements")}
    assert {"expires_at", "plan_period"}.issubset(entitlement_columns)


def test_stale_schedule_moves_to_today_without_replaying_old_days():
    now = datetime(2026, 9, 10, 13, 0)
    stale = datetime(2026, 9, 7, 12, 0)
    assert worker_module._current_schedule(stale, now) == datetime(2026, 9, 10, 12, 0)


def test_stale_in_flight_item_is_not_requeued():
    with TestClient(app) as client:
        register(client, "stale_sender", "stale-sender@example.com")
        user_id = client.get("/api/me").json()["id"]
        account_id = client.post("/api/accounts", json={"name": "中断保护号"}).json()["id"]
        task = client.post(
            "/api/tasks",
            json={"account_id": account_id, "name": "中断保护计划", "send_time": "04:33", "recipients": [{"contact_name": "好友中断", "message": "今日火花"}]},
        ).json()
        with SessionLocal() as db:
            run = Run(
                task_id=task["id"],
                user_id=user_id,
                scheduled_for=utcnow() - timedelta(hours=1),
                status="running",
                claimed_at=utcnow() - timedelta(minutes=30),
                started_at=utcnow() - timedelta(minutes=30),
            )
            run.items = [RunItem(contact_name="好友中断", message="今日火花", status="sending")]
            db.add(run)
            db.commit()
            run_id = run.id

        claim_one()

        with SessionLocal() as db:
            recovered = db.get(Run, run_id)
            assert recovered.status == "needs_review"
            assert recovered.items[0].status == "uncertain"
            assert recovered.error_code == "WORKER_INTERRUPTED"


def test_retry_parent_is_settled_after_success():
    with SessionLocal() as db:
        task = db.scalar(select(Task).order_by(Task.created_at.desc()))
        parent = Run(task_id=task.id, user_id=task.user_id, scheduled_for=utcnow(), status="retrying", attempt=1)
        child = Run(task_id=task.id, user_id=task.user_id, scheduled_for=utcnow(), status="success", attempt=2)
        db.add_all([parent, child])
        db.flush()
        child.parent_run_id = parent.id
        worker_module._settle_retry_ancestors(db, child)
        assert parent.status == "recovered"
        db.rollback()


def test_login_is_rate_limited_after_repeated_failures():
    identity = "rate-limit-target@example.com"
    with TestClient(app) as client:
        for _ in range(main_module.LOGIN_FAILURE_LIMIT):
            response = client.post("/api/auth/login", json={"username": identity, "password": "wrong"})
            assert response.status_code == 401
        limited = client.post("/api/auth/login", json={"username": identity, "password": "wrong"})
        assert limited.status_code == 429
    main_module._login_failures.clear()


def test_contact_sync_worker_upserts_structured_contacts(monkeypatch):
    with TestClient(app) as client:
        register(client, "sync_user", "sync@example.com")
        user_id = client.get("/api/me").json()["id"]
        account_id = client.post("/api/accounts", json={"name": "同步测试号"}).json()["id"]
        with SessionLocal() as db:
            account = db.get(DouyinAccount, account_id)
            account.state_ciphertext = "encrypted-state-placeholder"
            account.status = "valid"
            sync = ContactSync(account_id=account_id, user_id=user_id)
            db.add(sync)
            db.commit()

        monkeypatch.setattr(worker_module, "decrypt_text", lambda _value: "{}")
        monkeypatch.setattr(worker_module, "list_contacts", lambda _state, profile_key=None: ([
            {"display_name": "同步好友", "conversation_type": "friend", "source_key": "sec-1", "external_id": "sec-1", "streak_days": 23},
            {"display_name": "同步群", "conversation_type": "group", "source_key": "chat-2", "external_id": "chat-2"},
        ], None, "{}"))
        assert worker_module.process_contact_sync() is True
        contacts = client.get(f"/api/accounts/{account_id}/contacts").json()["contacts"]
        assert [(item["display_name"], item["conversation_type"]) for item in contacts] == [("同步好友", "friend"), ("同步群", "group")]
        assert contacts[0]["streak_days"] == 23

        friend_id = next(item["id"] for item in contacts if item["display_name"] == "同步好友")
        corrected = client.patch(f"/api/accounts/{account_id}/contacts/{friend_id}", json={"conversation_type": "group"})
        assert corrected.status_code == 200
        assert corrected.json()["type_locked"] is True
        with SessionLocal() as db:
            db.add(ContactSync(account_id=account_id, user_id=user_id))
            db.commit()
        assert worker_module.process_contact_sync() is True
        refreshed = client.get(f"/api/accounts/{account_id}/contacts").json()["contacts"]
        corrected_again = next(item for item in refreshed if item["id"] == friend_id)
        assert corrected_again["conversation_type"] == "group"
        assert corrected_again["type_locked"] is True
