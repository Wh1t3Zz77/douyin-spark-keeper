from sqlalchemy import select
import pytest
from app import worker, douyin
from app.models import Run, EmailOutbox, Task
from unittest.mock import Mock
from test_failure_recovery import recovery_db, seed as _seed


@pytest.fixture(autouse=True)
def resource_test_clock(monkeypatch):
    from app.models import utcnow
    now=utcnow().replace(hour=12,minute=0,second=0,microsecond=0)
    monkeypatch.setattr(worker,'utcnow',lambda:now)


def seed(db,user_id='one',count=2):
    run=_seed(db,user_id,count)
    run.scheduled_for=worker.utcnow()
    db.commit()
    return run


@pytest.mark.parametrize('user_id', ['one', 'another'])
def test_pressure_stop_retries_all_confirmed_unsent_then_recovers(recovery_db, monkeypatch, user_id):
    db=recovery_db;run=seed(db,user_id,count=5)
    original_schedule=db.get(Task,run.task_id).next_run_at
    run.items[0].status='failed'
    run.items[0].reason='identity check stopped before submission'
    original=[(i.contact_id,i.external_id,i.message) for i in run.items]
    db.commit()
    worker.settle_interrupted_run(run.id,'RUN_RESOURCE_PRESSURE')
    worker.settle_interrupted_run(run.id,'RUN_RESOURCE_PRESSURE')
    db.expire_all()
    children=db.scalars(select(Run).where(Run.parent_run_id==run.id)).all()
    assert len(children)==1
    child=children[0]
    assert child.attempt==1 and child.status=='pending'
    assert [(i.contact_id,i.external_id,i.message) for i in child.items]==original
    assert all(i.status=='failed' for i in db.get(Run,run.id).items)
    assert db.get(Run,run.id).status=='retrying'
    assert not db.scalars(select(EmailOutbox)).all()
    assert db.get(Task,run.task_id).next_run_at==original_schedule
    def send(state,recipients,before,result,**kwargs):
        for name,message,identity,item_id in recipients:
            before(item_id);result(douyin.SendResult(name,'submitted',item_id=item_id))
        return [],None,''
    monkeypatch.setattr(worker,'send_batch',send)
    child.status='running';db.commit()
    worker.execute_run(child.id);db.expire_all()
    assert db.get(Run,child.id).status=='success'
    assert db.get(Run,run.id).status=='recovered'
    assert not db.scalars(select(EmailOutbox)).all()


def test_pressure_stop_preserves_submitted_and_retries_only_remaining(recovery_db):
    db=recovery_db;run=seed(db,count=3)
    run.items[0].status='submitted';run.items[1].status='failed';db.commit()
    submitted_contact=run.items[0].contact_id
    worker.settle_interrupted_run(run.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
    child=db.scalar(select(Run).where(Run.parent_run_id==run.id))
    assert child and len(child.items)==2
    assert submitted_contact not in {i.contact_id for i in child.items}
    assert db.get(Run,run.id).items[0].status=='submitted'
    assert not db.scalars(select(EmailOutbox)).all()


def test_resource_deferrals_do_not_exhaust_business_retry_budget(recovery_db):
    db=recovery_db;root=seed(db,count=5);root.attempt=3;db.commit();current=root
    for attempt in range(8):
        current.status='running';db.commit()
        worker.settle_interrupted_run(current.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
        saved=db.get(Run,current.id)
        assert saved.status=='retrying'
        assert not db.scalars(select(EmailOutbox)).all()
        current=db.scalar(select(Run).where(Run.parent_run_id==current.id))
        assert current and current.attempt==3 and len(current.items)==5
        assert current.error_code=='RESOURCE_RETRY_WAIT'


def test_pressure_stop_with_only_known_submissions_is_success(recovery_db):
    db=recovery_db;run=seed(db)
    for item in run.items:item.status='submitted'
    db.commit()
    worker.settle_interrupted_run(run.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
    assert db.get(Run,run.id).status=='success'
    assert db.get(Run,run.id).error_code is None
    assert not db.scalars(select(EmailOutbox)).all()


def test_resource_deferrals_back_off_and_never_cross_midnight(recovery_db,monkeypatch):
    from datetime import datetime,timedelta
    db=recovery_db;run=seed(db);now=run.scheduled_for.replace(hour=12,minute=0)
    run.scheduled_for=now;db.commit();monkeypatch.setattr(worker,'utcnow',lambda:now)
    for minutes in [5,10,20,30,30]:
        run.status='running';db.commit()
        worker.settle_interrupted_run(run.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
        run=db.scalar(select(Run).where(Run.parent_run_id==run.id))
        assert run.scheduled_for==now+timedelta(minutes=minutes)
    now=now.replace(hour=15,minute=57)
    run.status='running';db.commit()
    worker.settle_interrupted_run(run.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
    assert db.get(Run,run.id).status=='failed'
    assert db.scalar(select(Run).where(Run.parent_run_id==run.id)) is None
    assert len(db.scalars(select(EmailOutbox)).all())==2


def test_overdue_resource_wait_expires_after_restart_instead_of_replaying_yesterday(recovery_db,monkeypatch):
    from datetime import timedelta
    db=recovery_db;root=seed(db)
    worker.settle_interrupted_run(root.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
    child=db.scalar(select(Run).where(Run.parent_run_id==root.id))
    monkeypatch.setattr(worker,'utcnow',lambda:worker._resource_retry_deadline(db,child)+timedelta(minutes=1))
    assert worker.claim_one() is None
    db.expire_all()
    assert db.get(Run,child.id).error_code=='RESOURCE_RETRY_EXPIRED'
    assert db.get(Run,root.id).status=='failed'
    assert all(i.status=='failed' for i in db.get(Run,child.id).items)
    assert len(db.scalars(select(EmailOutbox)).all())==2


def test_disabled_plan_is_not_sent_by_deferred_recovery(recovery_db,monkeypatch):
    db=recovery_db;root=seed(db)
    worker.settle_interrupted_run(root.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
    child=db.scalar(select(Run).where(Run.parent_run_id==root.id))
    db.get(Task,root.task_id).enabled=False;db.commit()
    send=Mock();monkeypatch.setattr(worker,'send_batch',send)
    worker.execute_run(child.id);db.expire_all()
    send.assert_not_called()
    assert db.get(Run,child.id).status=='cancelled'


def test_removed_recipient_is_not_sent_after_resource_wait(recovery_db,monkeypatch):
    db=recovery_db;root=seed(db,count=2)
    worker.settle_interrupted_run(root.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
    child=db.scalar(select(Run).where(Run.parent_run_id==root.id))
    task=db.get(Task,root.task_id);removed=task.recipients[0].contact_id
    task.recipients[0].enabled=False;db.commit();seen=[]
    def send(state,recipients,before,result,**kw):
        for name,message,identity,item_id in recipients:
            seen.append(item_id);before(item_id);result(douyin.SendResult(name,'submitted',item_id=item_id))
        return [],None,''
    monkeypatch.setattr(worker,'send_batch',send)
    worker.execute_run(child.id);db.expire_all()
    saved=db.get(Run,child.id)
    assert len(seen)==1
    assert next(i for i in saved.items if i.contact_id==removed).status=='skipped'


def test_navigation_timeout_does_not_invalidate_account(monkeypatch):
    from playwright.sync_api import Error
    page=Mock();page.goto.side_effect=Error('synthetic network timeout')
    monkeypatch.setattr(douyin,'security_challenge',lambda _:None)
    login=Mock(return_value=(False,'not a loaded chat page'))
    monkeypatch.setattr(douyin,'login_state',login)
    assert douyin._load_send_chat(page).startswith('transient:')
    assert page.goto.call_count==3
    login.assert_not_called()


def test_initial_chat_list_timeout_recovers_before_sending(monkeypatch):
    from playwright.sync_api import Error
    page=Mock();page.wait_for_selector.side_effect=[Error('synthetic first load'),None]
    monkeypatch.setattr(douyin,'security_challenge',lambda _:None)
    monkeypatch.setattr(douyin,'login_state',lambda _:(True,''))
    assert douyin._load_send_chat(page) is None
    assert page.goto.call_count==2


@pytest.mark.parametrize('full,available,expected_samples',[(15,350000,10),(45,240000,3),(0,90000,3)])
def test_pressure_grace_distinguishes_moderate_from_critical(monkeypatch,full,available,expected_samples):
    process=Mock();process.args=['synthetic']
    process.wait.side_effect=worker.subprocess.TimeoutExpired('synthetic',2)
    monkeypatch.setattr(worker,'_host_memory_under_pressure',lambda:True)
    monkeypatch.setattr(worker,'_memory_pressure_snapshot',lambda:{'host_full_avg10':full,'available_kib':available})
    with pytest.raises(worker.BrowserResourcePressure):worker._wait_for_run_process(process)
    assert process.wait.call_count==expected_samples


def test_decaying_short_pressure_burst_can_finish_without_interruption(monkeypatch):
    process=Mock();process.args=['synthetic']
    process.wait.side_effect=[worker.subprocess.TimeoutExpired('synthetic',2) for _ in range(4)]+[0]
    monkeypatch.setattr(worker,'_host_memory_under_pressure',Mock(side_effect=[True,True,True,False]))
    monkeypatch.setattr(worker,'_memory_pressure_snapshot',Mock(side_effect=[
        {'host_full_avg10':14.73,'available_kib':332728},
        {'host_full_avg10':12.42,'available_kib':352856},
        {'host_full_avg10':10.35,'available_kib':361384},
    ]))
    assert worker._wait_for_run_process(process)==0
    assert process.wait.call_count==5
