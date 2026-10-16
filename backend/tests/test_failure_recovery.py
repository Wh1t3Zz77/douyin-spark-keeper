from datetime import timedelta
import os
from types import SimpleNamespace
from unittest.mock import Mock
import smtplib
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app import worker, douyin, mailer
from app.database import Base
from app.identity import snapshot_item
from app.models import User, UserEntitlement, DouyinAccount, Contact, Task, TaskRecipient, Run, EmailOutbox, NotificationPreference, utcnow


@pytest.fixture
def recovery_db(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(worker, 'SessionLocal', lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr(worker, 'decrypt_text', lambda _: '{}')
    monkeypatch.setattr(worker, 'encrypt_text', lambda _: 'synthetic')
    monkeypatch.setattr(worker, 'get_settings', lambda: SimpleNamespace(task_failure_admin_email='ops@example.test'))
    with Session(engine, expire_on_commit=False) as db:
        yield db


def seed(db, user_id='one', count=2):
    db.add(User(id=user_id, username=user_id, email=f'{user_id}@example.test', password_hash='unused'))
    db.add(UserEntitlement(user_id=user_id, plan_tier='pro', expires_at=utcnow()+timedelta(days=10)))
    db.add(NotificationPreference(user_id=user_id, enabled=True, task_failed=True, login_expired=True))
    db.add(DouyinAccount(id='account-'+user_id, user_id=user_id, name='synthetic', status='valid', state_ciphertext='synthetic'))
    task=Task(user_id=user_id, account_id='account-'+user_id, name='synthetic plan', send_time='00:00', next_run_at=utcnow()+timedelta(days=1))
    for i in range(count):
        contact=Contact(id=f'{user_id}-{i}', user_id=user_id, account_id=task.account_id, source_key=f'conv:{i}', external_id=f'conv:{i}', display_name='synthetic friend')
        db.add(contact)
        task.recipients.append(TaskRecipient(contact_id=contact.id, contact_name='synthetic friend', message='synthetic message'))
    db.add(task); db.flush()
    run=Run(task_id=task.id, user_id=user_id, scheduled_for=utcnow(), status='running')
    run.items=[snapshot_item(db,task,r) for r in task.recipients]
    db.add(run); db.commit()
    return run


@pytest.mark.parametrize('user_id', ['one','another'])
def test_navigation_failure_retries_real_items_then_notifies(recovery_db, monkeypatch, user_id):
    db=recovery_db; root=seed(db,user_id)
    monkeypatch.setattr(worker,'send_batch',Mock(side_effect=RuntimeError('synthetic navigation timeout')))
    current=root
    for attempt in range(1,4):
        current.status='running';db.commit()
        worker.execute_run(current.id);db.expire_all()
        current=db.get(Run,current.id)
        assert all(i.status=='failed' for i in current.items)
        emails=db.scalars(select(EmailOutbox)).all()
        if attempt<3:
            child=db.scalar(select(Run).where(Run.parent_run_id==current.id))
            assert child and len(child.items)==2 and child.attempt==attempt+1
            assert not emails
            current=child
        else:
            assert current.status=='failed' and current.error_code=='RETRIES_EXHAUSTED'
            assert {e.recipient for e in emails}=={f'{user_id}@example.test','ops@example.test'}
            assert db.get(Run,root.id).status=='failed'
            worker.notify_failure(db,current,'duplicate','synthetic','task_failed')
            db.flush()
            assert len(db.scalars(select(EmailOutbox)).all())==2


def test_partial_submission_and_inflight_exception_never_retries_uncertain(recovery_db,monkeypatch):
    db=recovery_db;run=seed(db,count=3)
    def send(state, recipients, before, result, **kwargs):
        first=recipients[0];before(first[3])
        result(douyin.SendResult(first[0],'submitted',item_id=first[3]))
        before(recipients[1][3])
        raise RuntimeError('synthetic interruption')
    monkeypatch.setattr(worker,'send_batch',send)
    worker.execute_run(run.id);db.expire_all()
    saved=db.get(Run,run.id)
    assert sorted(i.status for i in saved.items)==['failed','submitted','uncertain']
    assert saved.status=='needs_review'
    assert db.scalar(select(Run).where(Run.parent_run_id==run.id)) is None
    assert len(db.scalars(select(EmailOutbox)).all())==2


def test_context_cleanup_error_does_not_resend_submitted_messages(recovery_db,monkeypatch):
    db=recovery_db;run=seed(db)
    def send(state,recipients,before,result,**kwargs):
        for name,message,identity,item_id in recipients:
            before(item_id);result(douyin.SendResult(name,'submitted',item_id=item_id))
        raise RuntimeError('synthetic context cleanup failure')
    monkeypatch.setattr(worker,'send_batch',send)
    worker.execute_run(run.id);db.expire_all()
    assert db.get(Run,run.id).status=='success'
    assert not db.scalars(select(EmailOutbox)).all()
    assert db.scalar(select(Run).where(Run.parent_run_id==run.id)) is None


def test_missing_batch_results_do_not_silently_skip(recovery_db,monkeypatch):
    db=recovery_db;run=seed(db)
    monkeypatch.setattr(worker,'send_batch',lambda *args,**kwargs:([],None,''))
    worker.execute_run(run.id);db.expire_all()
    child=db.scalar(select(Run).where(Run.parent_run_id==run.id))
    assert child and len(child.items)==2
    assert all(i.status=='failed' for i in db.get(Run,run.id).items)


def test_retry_guard_refuses_empty_or_inflight_items(recovery_db):
    db=recovery_db;run=seed(db)
    with pytest.raises(ValueError):worker._create_retry(db,run,[])
    run.items[0].status='sending'
    with pytest.raises(ValueError):worker._create_retry(db,run,[run.items[0]])
    assert db.scalar(select(Run).where(Run.parent_run_id==run.id)) is None


def test_empty_run_is_terminal_and_notifies(recovery_db,monkeypatch):
    db=recovery_db;run=seed(db);run.items.clear();db.commit()
    send=Mock();monkeypatch.setattr(worker,'send_batch',send)
    worker.execute_run(run.id);db.expire_all()
    assert db.get(Run,run.id).error_code=='EMPTY_RUN'
    assert len(db.scalars(select(EmailOutbox)).all())==2
    send.assert_not_called()


@pytest.mark.parametrize('error_code',['RUN_TIMEOUT','WORKER_EXITED'])
def test_interruption_settles_all_retry_ancestors_once(recovery_db,error_code):
    db=recovery_db;root=seed(db);root.status='retrying'
    middle=Run(task_id=root.task_id,user_id=root.user_id,scheduled_for=utcnow(),status='retrying',parent_run_id=root.id,attempt=2)
    db.add(middle);db.flush()
    child=Run(task_id=root.task_id,user_id=root.user_id,scheduled_for=utcnow(),status='running',parent_run_id=middle.id,attempt=3)
    child.items=[snapshot_item(db,db.get(Task,root.task_id),r) for r in db.get(Task,root.task_id).recipients]
    child.items[0].status='sending';db.add(child);db.commit()
    worker.settle_interrupted_run(child.id,error_code);worker.settle_interrupted_run(child.id,error_code);db.expire_all()
    assert all(db.get(Run,r.id).status=='needs_review' for r in (root,middle,child))
    assert all(db.get(Run,r.id).error_code==error_code for r in (root,middle,child))
    assert sorted(i.status for i in db.get(Run,child.id).items)==['skipped','uncertain']
    assert len(db.scalars(select(EmailOutbox)).all())==2


def test_login_failure_during_retry_settles_parent(recovery_db):
    db=recovery_db;root=seed(db);root.status='retrying'
    child=Run(task_id=root.task_id,user_id=root.user_id,scheduled_for=utcnow(),status='running',parent_run_id=root.id,attempt=2)
    db.add(child);db.get(DouyinAccount,'account-one').status='invalid';db.commit()
    worker.execute_run(child.id);db.expire_all()
    assert db.get(Run,root.id).status=='needs_account'
    assert len(db.scalars(select(EmailOutbox)).all())==2


def test_stale_claim_settles_retry_parent(recovery_db):
    db=recovery_db;root=seed(db);root.status='retrying'
    child=Run(task_id=root.task_id,user_id=root.user_id,scheduled_for=utcnow(),status='running',parent_run_id=root.id,attempt=2,claimed_at=utcnow()-timedelta(minutes=30))
    child.items=[snapshot_item(db,db.get(Task,root.task_id),db.get(Task,root.task_id).recipients[0])]
    child.items[0].status='sending';db.add(child);db.commit()
    assert worker.claim_one() is None
    db.expire_all()
    assert db.get(Run,root.id).status=='needs_review'


def test_lookup_batches_reads_and_rechecks_a_unique_identity(monkeypatch):
    page=Mock();titles=page.locator.return_value
    titles.evaluate_all.return_value=[2]
    assert douyin._find_conversation_title(page,'mutable display','conv:stable')[0] is titles.nth.return_value
    titles.evaluate_all.assert_called_once_with(douyin.CONVERSATION_MATCHES,'conv:stable')
    titles.nth.assert_called_once_with(2)
    titles.count.assert_not_called()


@pytest.mark.parametrize('phase,expected',[('input','failed'),('submission','uncertain')])
def test_submission_exception_distinguishes_safe_retry_from_unknown_delivery(monkeypatch,phase,expected):
    page=Mock();box=page.locator.return_value.first
    box.count.return_value=1;box.is_visible.return_value=True
    box.inner_text.return_value='synthetic message'
    monkeypatch.setattr(douyin,'_open_contact',lambda *args:True)
    monkeypatch.setattr(douyin,'security_challenge',lambda *args:None)
    monkeypatch.setattr(douyin,'_verify_conversation',lambda *args:True)
    if phase=='input':box.click.side_effect=RuntimeError('synthetic input error')
    else:
        def press(key):
            if key=='Enter':raise RuntimeError('synthetic response lost')
        page.keyboard.press.side_effect=press
    assert douyin._send_one(page,'synthetic friend','synthetic message','conv:synthetic').status==expected


def test_lookup_refuses_duplicate_stable_identity():
    page=Mock();page.locator.return_value.evaluate_all.return_value=[0,1]
    assert douyin._find_conversation_title(page,'same','conv:stable') is None
    page.locator.return_value.nth.assert_not_called()


def test_lookup_stops_scanning_when_budget_is_used(monkeypatch):
    clock=[0.0];page=Mock()
    page.wait_for_timeout.side_effect=lambda ms:clock.__setitem__(0,clock[0]+ms/1000)
    def scan(*args):clock[0]+=20;return []
    page.locator.return_value.evaluate_all.side_effect=scan
    monkeypatch.setattr(douyin,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    assert douyin._find_conversation_title(page,'same','conv:missing') is None
    assert page.locator.return_value.evaluate_all.call_count==3


def test_browser_batch_identity_matching_and_recycled_row_rejection():
    from playwright.sync_api import Error, sync_playwright
    with sync_playwright() as p:
        try:
            browser=p.chromium.launch(headless=True, executable_path=os.environ.get('TEST_BROWSER_EXECUTABLE'), args=['--no-sandbox','--disable-dev-shm-usage'])
        except Error as exc:
            if "Executable doesn't exist" in str(exc): pytest.skip('Playwright Chromium is not installed')
            raise
        page=browser.new_page()
        page.set_content(''.join(f'<div data-e2e="conversation-item" id="row{i}"><span class="conversationConversationItemtitle">same</span></div>' for i in range(100)))
        page.evaluate('''() => document.querySelectorAll('[data-e2e="conversation-item"]').forEach((row,i)=>{
            const conversation={id:'c'+i};
            Object.defineProperty(conversation,'toParticipantUserId',{get:()=> 'u'+i});
            row.__reactFiber$test={memoizedProps:{conversation}};
        })''')
        titles=page.locator('.conversationConversationItemtitle')
        assert titles.evaluate_all(douyin.CONVERSATION_MATCHES,'conv:c70')==[70]
        assert titles.evaluate_all(douyin.CONVERSATION_MATCHES,'uid:u70')==[70]
        assert titles.evaluate_all(douyin.CONVERSATION_MATCHES,'conv:missing')==[]
        page.evaluate("document.querySelector('#row99').__reactFiber$test.memoizedProps.conversation.id='c70'")
        assert douyin._find_conversation_title(page,'same','conv:c70') is None
        page.evaluate("document.querySelector('#row99').style.display='none'")
        assert titles.evaluate_all(douyin.CONVERSATION_MATCHES,'conv:c70')==[70]
        page.evaluate("document.querySelector('#row70').__reactFiber$test.memoizedProps.conversation.id='recycled'")
        assert not douyin._click_conversation_title(page,titles.nth(70),'conv:c70')
        browser.close()


def test_smtp_retry_logs_survive_success_without_private_details(recovery_db,monkeypatch,caplog):
    db=recovery_db;seed(db)
    item=EmailOutbox(recipient='private@example.test',subject='synthetic',body='private body')
    db.add(item);db.commit()
    caplog.set_level('INFO')
    monkeypatch.setattr(mailer,'send_one',Mock(side_effect=smtplib.SMTPResponseException(451,b'private diagnostic')))
    mailer.process_outbox(db)
    assert item.status=='retry' and item.attempts==1
    assert 'smtp_code=451' in caplog.text
    assert 'private diagnostic' not in caplog.text and 'private@example.test' not in caplog.text
    item.next_attempt_at=utcnow()-timedelta(seconds=1);db.commit()
    monkeypatch.setattr(mailer,'send_one',lambda _:None)
    mailer.process_outbox(db)
    assert item.status=='sent' and item.last_error is None
    assert 'SMTP accepted email' in caplog.text and 'smtp_code=451' in caplog.text
