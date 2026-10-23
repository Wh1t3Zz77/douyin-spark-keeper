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


@pytest.mark.parametrize('error_code',['RUN_TIMEOUT','WORKER_EXITED','RUN_RESOURCE_PRESSURE'])
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
    expected='failed' if error_code=='RUN_RESOURCE_PRESSURE' else 'skipped'
    assert sorted(i.status for i in db.get(Run,child.id).items)==[expected,'uncertain']
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


@pytest.mark.parametrize('phase',['lookup','input','identity','challenge'])
def test_pre_submission_failures_never_mark_item_as_sending(monkeypatch,phase):
    page=Mock();box=page.locator.return_value.first
    box.count.return_value=1;box.is_visible.return_value=True
    box.inner_text.return_value='synthetic message'
    monkeypatch.setattr(douyin,'_open_contact',lambda *a:phase!='lookup')
    monkeypatch.setattr(douyin,'_verify_conversation',lambda *a:phase!='identity')
    monkeypatch.setattr(douyin,'security_challenge',lambda *a:'verification required' if phase=='challenge' else None)
    if phase=='input':box.click.side_effect=RuntimeError('synthetic input failed')
    before=Mock()
    value=douyin._send_one(page,'same','synthetic message','conv:one',before)
    assert value.status in ['failed','security_challenge']
    before.assert_not_called()
    assert not any(c.args==('Enter',) for c in page.keyboard.press.call_args_list)


def test_database_send_marker_commits_only_immediately_before_enter(monkeypatch):
    page=Mock();box=page.locator.return_value.first;events=[]
    box.count.return_value=1;box.is_visible.return_value=True
    box.inner_text.side_effect=['synthetic message','']
    monkeypatch.setattr(douyin,'_open_contact',lambda *a:True)
    monkeypatch.setattr(douyin,'security_challenge',lambda *a:None)
    monkeypatch.setattr(douyin,'_verify_conversation',lambda *a:events.append('identity') or True)
    page.keyboard.press.side_effect=lambda k:events.append('enter') if k=='Enter' else None
    value=douyin._send_one(page,'same','synthetic message','conv:one',lambda:events.append('committed'))
    assert value.status=='submitted'
    assert events==['identity','committed','enter']


def test_send_marker_failure_prevents_submission(monkeypatch):
    page=Mock();box=page.locator.return_value.first
    box.count.return_value=1;box.is_visible.return_value=True;box.inner_text.return_value='synthetic message'
    monkeypatch.setattr(douyin,'_open_contact',lambda *a:True)
    monkeypatch.setattr(douyin,'security_challenge',lambda *a:None)
    monkeypatch.setattr(douyin,'_verify_conversation',lambda *a:True)
    value=douyin._send_one(page,'same','synthetic message','conv:one',Mock(side_effect=RuntimeError('synthetic database unavailable')))
    assert value.status=='failed'
    assert not any(c.args==('Enter',) for c in page.keyboard.press.call_args_list)


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


def test_lookup_can_wait_for_late_loaded_conversations_within_budget(monkeypatch):
    clock=[0.0];page=Mock();titles=page.locator.return_value
    page.wait_for_timeout.side_effect=lambda ms:clock.__setitem__(0,clock[0]+ms/1000)
    titles.evaluate_all.side_effect=[[] for _ in range(40)]+[[2]]
    monkeypatch.setattr(douyin,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    assert douyin._find_conversation_title(page,'same','conv:late')[0] is titles.nth.return_value
    assert titles.evaluate_all.call_count==41
    assert clock[0]<45


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


def test_lookup_scrolls_real_virtual_list_without_skipping_short_viewports():
    from playwright.sync_api import Error,sync_playwright
    with sync_playwright() as p:
        try:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('TEST_BROWSER_EXECUTABLE'),args=['--no-sandbox','--disable-dev-shm-usage'])
        except Error as exc:
            if "Executable doesn't exist" in str(exc):pytest.skip('Playwright Chromium is not installed')
            raise
        page=browser.new_page(viewport={'width':1100,'height':700})
        page.set_content('''<div id="list" style="position:absolute;left:620px;top:80px;width:280px;height:160px;overflow-y:auto">
          <div style="height:1600px;position:relative" id="rows"></div></div>
          <script>
          const list=document.querySelector('#list'),rows=document.querySelector('#rows');
          function render(){rows.innerHTML='';const first=Math.floor(list.scrollTop/40);
            for(let i=first;i<Math.min(40,first+5);i++){
              const title=document.createElement('div');title.className='conversationConversationItemtitle';
              title.style='position:absolute;height:40px;top:'+i*40+'px';title.textContent='same';
              title.__reactFiber$test={memoizedProps:{conversation:{id:'c'+i}}};rows.append(title);
            }}list.addEventListener('scroll',render);render();</script>''')
        match=douyin._find_conversation_title(page,'same','conv:c25')
        assert match is not None
        assert match[0].evaluate(douyin.IDENTITY_READER)['conversation_id']=='c25'
        assert page.locator('#list').evaluate('(node)=>node.scrollTop')>0
        browser.close()


def test_lookup_wheels_actual_list_to_request_missing_lazy_page():
    from playwright.sync_api import Error,sync_playwright
    with sync_playwright() as p:
        try:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('TEST_BROWSER_EXECUTABLE'),args=['--no-sandbox','--disable-dev-shm-usage'])
        except Error as exc:
            if "Executable doesn't exist" in str(exc):pytest.skip('Playwright Chromium is not installed')
            raise
        page=browser.new_page(viewport={'width':1100,'height':700})
        page.set_content('''<div id="list" style="position:absolute;left:620px;top:80px;width:280px;height:160px;overflow-y:auto">
          <div style="height:160px;position:relative" id="rows"></div></div>
          <script>
          const list=document.querySelector('#list'),rows=document.querySelector('#rows');
          function render(){rows.innerHTML='';const first=Math.floor(list.scrollTop/40);
            for(let i=first;i<Math.min(rows.style.height==='160px'?2:40,first+5);i++){
              const title=document.createElement('div');title.className='conversationConversationItemtitle';
              title.style='position:absolute;height:40px;top:'+i*40+'px';title.textContent='same';
              title.__reactFiber$test={memoizedProps:{conversation:{id:'c'+i}}};rows.append(title);
            }}list.addEventListener('scroll',render);list.addEventListener('wheel',()=>{rows.style.height='1600px';render()});render();</script>''')
        match=douyin._find_conversation_title(page,'same','conv:c25')
        assert match is not None
        assert match[0].evaluate(douyin.IDENTITY_READER)['conversation_id']=='c25'
        assert page.locator('#list').evaluate('(node)=>node.scrollTop')>0
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


def test_send_asset_filter_keeps_chat_scripts_data_and_identity():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from playwright.sync_api import Error, sync_playwright
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if self.path=='/':
                body=b'''<script src="/chat.js"></script><img src="/large.png">
                <video src="/large.mp4" preload="auto"></video>
                <div class="conversationConversationItemtitle" id="friend">same</div>
                <div contenteditable="true">text control</div>'''
                kind='text/html'
            elif self.path=='/chat.js':
                body=b"window.chatReady=true; fetch('/chat-data').then(r=>r.text()).then(x=>window.chatData=x)"
                kind='application/javascript'
            else:body=b'synthetic';kind='text/plain'
            self.send_response(200);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            try:
                browser=p.chromium.launch(headless=True,executable_path=os.environ.get('TEST_BROWSER_EXECUTABLE'),args=['--no-sandbox','--disable-dev-shm-usage'])
            except Error as exc:
                if "Executable doesn't exist" in str(exc):pytest.skip('Playwright Chromium is not installed')
                raise
            context=browser.new_context();douyin._limit_send_assets(context)
            page=context.new_page();blocked=[]
            page.on('requestfailed',lambda request:blocked.append(request.resource_type))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.wait_for_function("window.chatReady && window.chatData === 'synthetic'")
            page.wait_for_function("document.querySelector('video').error !== null")
            page.evaluate("document.querySelector('#friend').__reactFiber$test={memoizedProps:{conversation:{id:'stable'}}}")
            assert page.locator('.conversationConversationItemtitle').evaluate_all(douyin.CONVERSATION_MATCHES,'conv:stable')==[0]
            page.locator('[contenteditable]').fill('synthetic message')
            assert page.locator('[contenteditable]').inner_text()=='synthetic message'
            assert {'image','media'} <= set(blocked)
            assert '/chat.js' in seen and '/chat-data' in seen
            assert '/large.png' not in seen and '/large.mp4' not in seen
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize('full,available,expected',[(45,240000,True),(0,90000,True),(0,500000,False)])
def test_memory_guard_detects_stalls_even_with_available_memory(monkeypatch,full,available,expected):
    def read(path):
        if path.name=='memory':return f'some avg10=50 avg60=1 avg300=1 total=1\nfull avg10={full} avg60=1 avg300=1 total=1'
        return f'MemAvailable: {available} kB\n'
    monkeypatch.setattr(worker.Path,'read_text',read)
    assert worker._host_memory_under_pressure() is expected


def test_pressure_monitor_requires_consecutive_samples(monkeypatch):
    process=Mock();process.args=['synthetic']
    timeout=lambda:worker.subprocess.TimeoutExpired('synthetic',2)
    process.wait.side_effect=[timeout(),timeout(),timeout(),timeout(),timeout(),0]
    monkeypatch.setattr(worker,'_host_memory_under_pressure',Mock(side_effect=[True,True,False,True,True]))
    assert worker._wait_for_run_process(process)==0


def test_pressure_monitor_stops_before_hard_deadline_and_settles(monkeypatch,recovery_db):
    db=recovery_db;run=seed(db);run.items[0].status='sending';db.commit()
    process=Mock();process.args=['synthetic']
    process.wait.side_effect=worker.subprocess.TimeoutExpired('synthetic',2)
    monkeypatch.setattr(worker.subprocess,'Popen',Mock(return_value=process))
    monkeypatch.setattr(worker,'_host_memory_under_pressure',lambda:True)
    stop=Mock(return_value=True);monkeypatch.setattr(worker,'_stop_run_process_group',stop)
    worker.execute_run_isolated(run.id);db.expire_all()
    assert process.wait.call_count==10
    stop.assert_called_once_with(process)
    assert db.get(Run,run.id).error_code=='RUN_RESOURCE_PRESSURE'
    assert sorted(i.status for i in db.get(Run,run.id).items)==['failed','uncertain']
    assert len(db.scalars(select(EmailOutbox)).all())==2


def test_pressure_monitor_preserves_overall_timeout(monkeypatch):
    process=Mock();process.args=['synthetic']
    process.wait.side_effect=worker.subprocess.TimeoutExpired('synthetic',2)
    monkeypatch.setattr(worker,'RUN_TIMEOUT_SECONDS',4)
    monkeypatch.setattr(worker,'time',SimpleNamespace(monotonic=Mock(side_effect=[0,0,2,4])))
    monkeypatch.setattr(worker,'_host_memory_under_pressure',lambda:False)
    with pytest.raises(worker.subprocess.TimeoutExpired):worker._wait_for_run_process(process)
    assert process.wait.call_count==2


@pytest.mark.parametrize('supervisor_error',[False,True])
def test_configured_worker_recycles_after_one_browser_run(monkeypatch,supervisor_error):
    monkeypatch.setenv('SPARK_RECYCLE_AFTER_RUN','1')
    monkeypatch.setattr(worker.Base.metadata,'create_all',Mock())
    monkeypatch.setattr(worker,'schedule_due_tasks',Mock())
    monkeypatch.setattr(worker,'process_daily_notifications',Mock())
    monkeypatch.setattr(worker,'_host_memory_under_pressure',lambda:False)
    claim=Mock(return_value='synthetic-run');monkeypatch.setattr(worker,'claim_one',claim)
    execute=Mock(side_effect=RuntimeError('synthetic supervisor error') if supervisor_error else None)
    monkeypatch.setattr(worker,'execute_run_isolated',execute)
    worker.main()
    claim.assert_called_once();execute.assert_called_once_with('synthetic-run')


def test_lookup_rescans_late_rows_loaded_above_current_viewport():
    from playwright.sync_api import Error,sync_playwright
    with sync_playwright() as p:
        try:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('TEST_BROWSER_EXECUTABLE'),args=['--no-sandbox','--disable-dev-shm-usage'])
        except Error as exc:
            if "Executable doesn't exist" in str(exc):pytest.skip('Playwright Chromium is not installed')
            raise
        page=browser.new_page(viewport={'width':1100,'height':700})
        page.set_content('''<div id="list" style="position:absolute;left:620px;top:80px;width:280px;height:160px;overflow-y:auto">
          <div style="height:1600px;position:relative" id="rows"></div></div>
          <script>
          const list=document.querySelector('#list'),rows=document.querySelector('#rows');
          function render(){rows.innerHTML='';const first=Math.floor(list.scrollTop/40);
            for(let i=first;i<Math.min(40,first+5);i++){
              const title=document.createElement('div');title.className='conversationConversationItemtitle';
              title.style='position:absolute;height:40px;top:'+i*40+'px';title.textContent='same';
              title.__reactFiber$test={memoizedProps:{conversation:{id:i===0&&window.ready?'late':'c'+i}}};rows.append(title);
            }}list.addEventListener('scroll',render);render();setTimeout(()=>{window.ready=true;render()},4000);</script>''')
        match=douyin._find_conversation_title(page,'same','conv:late')
        assert match is not None
        assert match[0].evaluate(douyin.IDENTITY_READER)['conversation_id']=='late'
        assert page.evaluate('Boolean(window.ready)')
        browser.close()
