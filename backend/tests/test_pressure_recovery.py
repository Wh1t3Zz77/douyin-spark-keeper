from sqlalchemy import select
import pytest
from app import worker, douyin
from app.models import Run, EmailOutbox, Task
from unittest.mock import Mock
from test_failure_recovery import recovery_db, seed


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
    assert child.attempt==2 and child.status=='pending'
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


def test_repeated_pressure_stops_after_three_attempts_and_notifies_once(recovery_db):
    db=recovery_db;root=seed(db,count=5);current=root
    for attempt in range(1,4):
        current.status='running';db.commit()
        worker.settle_interrupted_run(current.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
        saved=db.get(Run,current.id)
        if attempt<3:
            assert saved.status=='retrying'
            assert not db.scalars(select(EmailOutbox)).all()
            current=db.scalar(select(Run).where(Run.parent_run_id==current.id))
            assert current and current.attempt==attempt+1 and len(current.items)==5
        else:
            assert saved.status=='failed' and saved.error_code=='RUN_RESOURCE_PRESSURE'
            assert db.scalar(select(Run).where(Run.parent_run_id==saved.id)) is None
            assert db.get(Run,root.id).status=='failed'
            assert len(db.scalars(select(EmailOutbox)).all())==2
            worker.settle_interrupted_run(saved.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
            assert len(db.scalars(select(EmailOutbox)).all())==2


def test_pressure_stop_with_only_known_submissions_is_success(recovery_db):
    db=recovery_db;run=seed(db)
    for item in run.items:item.status='submitted'
    db.commit()
    worker.settle_interrupted_run(run.id,'RUN_RESOURCE_PRESSURE');db.expire_all()
    assert db.get(Run,run.id).status=='success'
    assert db.get(Run,run.id).error_code is None
    assert not db.scalars(select(EmailOutbox)).all()


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
