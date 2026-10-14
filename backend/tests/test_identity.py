import os
import json
from datetime import timedelta
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Contact, Task, TaskRecipient, Run, RunItem, DouyinAccount, User, utcnow
from app.identity import sync_contacts, snapshot_item, resolve_run_contact, contact_for_recipient, backfill_legacy_bindings
from app.identity_schema import migrate_identity_schema
from app.identity_dom import IDENTITY_READER, identity_matches, ProfileCollector
from app import douyin, worker


@pytest.mark.parametrize('enabled,task_failed,user_mail_count',[(True,True,1),(False,True,0),(True,False,0)])
def test_failure_admin_is_independent_and_notification_is_deduplicated(db,monkeypatch,enabled,task_failed,user_mail_count):
    from types import SimpleNamespace
    from app.models import NotificationPreference,EmailOutbox
    monkeypatch.setattr(worker,'get_settings',lambda:SimpleNamespace(task_failure_admin_email='owner@example.test'))
    db.add(NotificationPreference(user_id='u',enabled=enabled,task_failed=task_failed));db.flush()
    t=task(db);run=Run(task_id=t.id,user_id='u',scheduled_for=utcnow(),status='needs_review',error_code='CONTACT_IDENTITY_REQUIRED')
    db.add(run);db.flush()
    for _ in range(2):worker.notify_failure(db,run,'task failed','failure without private message','task_failed')
    emails=db.scalars(select(EmailOutbox)).all()
    assert len(emails)==1+user_mail_count
    admin=next(e for e in emails if e.recipient=='owner@example.test')
    assert 'fake' in admin.subject and 'fake@example.test' in admin.body and run.id in admin.body


def test_failure_same_user_and_admin_email_gets_one_copy(db,monkeypatch):
    from types import SimpleNamespace
    from app.models import NotificationPreference,EmailOutbox
    monkeypatch.setattr(worker,'get_settings',lambda:SimpleNamespace(task_failure_admin_email='FAKE@example.test'))
    db.add(NotificationPreference(user_id='u'));t=task(db)
    run=Run(task_id=t.id,user_id='u',scheduled_for=utcnow());db.add(run);db.flush()
    worker.notify_failure(db,run,'failure','body','task_failed')
    assert len(db.scalars(select(EmailOutbox)).all())==1


@pytest.mark.parametrize('fatal',[None,'transient:browser unavailable'])
def test_missing_identity_terminal_run_enqueues_both_notifications(db,monkeypatch,fatal):
    from types import SimpleNamespace
    from app.models import NotificationPreference,EmailOutbox
    monkeypatch.setattr(worker,'get_settings',lambda:SimpleNamespace(task_failure_admin_email='owner@example.test'))
    db.add(NotificationPreference(user_id='u',enabled=True,task_failed=True))
    t=task(db);run=Run(task_id=t.id,user_id='u',scheduled_for=utcnow(),status='running')
    run.items=[snapshot_item(db,t,t.recipients[0])];db.add(run);db.commit()
    engine=db.get_bind()
    monkeypatch.setattr(worker,'SessionLocal',lambda:Session(engine,expire_on_commit=False))
    monkeypatch.setattr(worker,'decrypt_text',lambda _: '{}')
    monkeypatch.setattr(worker,'send_batch',lambda *a,**kw:([],fatal,''))
    worker.execute_run(run.id);db.expire_all()
    assert db.get(Run,run.id).error_code=='CONTACT_IDENTITY_REQUIRED'
    emails=db.scalars(select(EmailOutbox)).all()
    assert {e.recipient for e in emails}=={'fake@example.test','owner@example.test'}
    assert all('test' not in e.body for e in emails if e.recipient=='fake@example.test')


def test_recent_sync_excludes_stale_numeric_placeholder(db):
    from app.identity import recent_contacts
    start=utcnow()
    old=contact(db,'1500000000000000',None)
    old.last_seen_at=start-timedelta(days=1)
    current=contact(db,'current','conv:current')
    current.last_seen_at=start+timedelta(seconds=1)
    stale=contact(db,'old visible friend','conv:stale')
    stale.last_seen_at=start-timedelta(days=1)
    assert [c.id for c in recent_contacts(db,'a','u',start)]==[current.id]
    assert db.get(Contact,old.id) is not None


def test_recent_sync_retains_saved_plans_and_selections(db):
    from app.identity import recent_contacts
    start=utcnow()
    bound=contact(db,'saved','conv:saved'); bound.last_seen_at=start-timedelta(days=1)
    task(db,contact=bound)
    selected=contact(db,'chosen','conv:chosen'); selected.selected=True; selected.last_seen_at=start-timedelta(days=1)
    legacy=contact(db,'old name',None); legacy.last_seen_at=start-timedelta(days=1)
    task(db,'old name')
    assert {c.id for c in recent_contacts(db,'a','u',start)}=={bound.id,selected.id,legacy.id}


def test_sync_rejects_only_unresolved_numeric_placeholders(db):
    from app.identity import recent_contacts
    sync_contacts(db,'a','u',[{'display_name':'1500000000000000'},
        raw('1600000000000000','conv:real'),{'display_name':'1700000000000000','nickname':'known nickname'}])
    assert {c.display_name for c in db.scalars(select(Contact)).all()}=={'1600000000000000','1700000000000000'}
    assert len(recent_contacts(db,'a','u'))==2


def test_recent_sync_is_scoped_to_account_and_owner(db):
    from app.identity import recent_contacts
    contact(db,'normal','conv:normal')
    assert recent_contacts(db,'other','u')==[]
    assert recent_contacts(db,'a','other')==[]


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(User(id='u', username='fake', email='fake@example.test', password_hash='unused', is_admin=True))
        session.add(DouyinAccount(id='a', user_id='u', name='fake', status='valid', state_ciphertext='dummy'))
        session.commit()
        yield session


def task(db, name='old', contact=None):
    t = Task(user_id='u', account_id='a', name='plan', send_time='00:00', next_run_at=utcnow())
    t.recipients = [TaskRecipient(contact_name=name, contact_id=contact.id if contact else None, message='test')]
    db.add(t); db.flush()
    return t


def contact(db, name='old', external='conv:one', **kw):
    c = Contact(user_id='u', account_id='a', display_name=name, source_key=external or 'name:'+name, external_id=external, **kw)
    db.add(c); db.flush()
    return c


def raw(name='new', external='conv:one', **kw):
    return {'display_name':name, 'external_id':external, 'conversation_id':'one', 'conversation_type':'friend', **kw}


def test_rename_keeps_contact_plan_and_run(db):
    c = contact(db); t = task(db, contact=c); item = snapshot_item(db,t,t.recipients[0])
    sync_contacts(db,'a','u',[raw()]); db.flush()
    assert len(db.scalars(select(Contact)).all()) == 1
    assert c.display_name == t.recipients[0].contact_name == 'new'
    assert resolve_run_contact(db,t,item).id == c.id
    assert item.contact_name == 'new'


def test_remark_and_handle_change_never_change_identity(db):
    c = contact(db, douyin_id='old_handle'); t=task(db,contact=c)
    sync_contacts(db,'a','u',[raw('remark', douyin_id='new_handle', remark_name='remark', nickname='nickname')])
    assert t.recipients[0].contact_id == c.id and c.douyin_id == 'new_handle'
    assert c.external_id == 'conv:one'


def test_same_name_contacts_are_distinct(db):
    sync_contacts(db,'a','u',[raw('same'),raw('same','conv:two', conversation_id='two')])
    contacts = db.scalars(select(Contact)).all()
    t=task(db,'same',contacts[0]); t.recipients.append(TaskRecipient(contact_name='same',contact_id=contacts[1].id,message='test'))
    db.flush()
    assert len(t.recipients)==2
    assert len({snapshot_item(db,t,r).external_id for r in t.recipients})==2


def test_legacy_unique_name_backfills(db):
    c=contact(db); t=task(db)
    backfill_legacy_bindings(db)
    assert t.recipients[0].contact_id == c.id


def test_legacy_same_name_never_guesses(db):
    contact(db); contact(db,external='conv:two'); t=task(db)
    assert contact_for_recipient(db,t,t.recipients[0]) is None
    assert t.recipients[0].contact_id is None


def test_missing_id_blocks_legacy(db):
    contact(db,external=None); t=task(db)
    assert contact_for_recipient(db,t,t.recipients[0]) is None


def test_duplicate_name_sync_cannot_upgrade_legacy(db):
    c=contact(db,external=None); t=task(db,contact=c)
    sync_contacts(db,'a','u',[raw('old'),raw('old','conv:two',conversation_id='two')])
    assert c.external_id is None
    assert resolve_run_contact(db,t,snapshot_item(db,t,t.recipients[0])) is None


def test_unique_sync_upgrades_legacy_row(db):
    c=contact(db,external=None); t=task(db,contact=c)
    sync_contacts(db,'a','u',[raw('old')])
    assert c.external_id == 'conv:one' and t.recipients[0].contact_id==c.id


def test_changed_identity_cannot_hijack_run(db):
    c=contact(db); t=task(db,contact=c); item=snapshot_item(db,t,t.recipients[0])
    c.external_id='conv:imposter'
    assert resolve_run_contact(db,t,item) is None


def test_cross_account_contact_rejected(db):
    c=contact(db); t=task(db,contact=c); c.account_id='other'
    assert resolve_run_contact(db,t,snapshot_item(db,t,t.recipients[0])) is None


def test_legacy_pending_item_resolves_unique_recipient(db):
    c=contact(db); t=task(db,contact=c); item=RunItem(contact_name='old',message='test')
    assert resolve_run_contact(db,t,item).id==c.id
    assert item.recipient_id==t.recipients[0].id


def test_legacy_pending_item_with_same_names_blocks(db):
    c=contact(db); t=task(db,contact=c); t.recipients.append(TaskRecipient(contact_name='old',message='test'))
    assert resolve_run_contact(db,t,RunItem(contact_name='old',message='test')) is None


def test_retry_copies_identity_snapshot(db):
    c=contact(db); t=task(db,contact=c); run=Run(task_id=t.id,user_id='u',scheduled_for=utcnow(),attempt=1)
    run.items=[snapshot_item(db,t,t.recipients[0])]; db.add(run); db.flush()
    worker._create_retry(db,run,run.items); db.flush()
    retry=db.scalar(select(Run).where(Run.parent_run_id==run.id))
    assert retry.items[0].contact_id == c.id
    assert retry.items[0].external_id == 'conv:one'
    assert retry.items[0].recipient_id == run.items[0].recipient_id


def test_legacy_untyped_id_upgrades_queued_snapshot(db):
    c=contact(db,external='one'); t=task(db,contact=c); item=snapshot_item(db,t,t.recipients[0])
    sync_contacts(db,'a','u',[raw()])
    assert resolve_run_contact(db,t,item).id==c.id
    assert item.external_id=='conv:one'


def test_fallback_sec_id_gains_conversation_metadata_without_rebinding(db):
    c=contact(db,external='sec:stable',sec_uid='stable'); t=task(db,contact=c)
    item=snapshot_item(db,t,t.recipients[0])
    sync_contacts(db,'a','u',[raw('renamed',sec_uid='stable')])
    assert len(db.scalars(select(Contact)).all())==1
    assert t.recipients[0].contact_id==c.id and t.recipients[0].contact_name=='renamed'
    assert resolve_run_contact(db,t,item).id==c.id


def test_fallback_uid_id_gains_conversation_metadata_without_rebinding(db):
    c=contact(db,external='uid:123',uid='123'); t=task(db,contact=c)
    sync_contacts(db,'a','u',[raw('renamed',uid='123')])
    assert len(db.scalars(select(Contact)).all())==1 and t.recipients[0].contact_id==c.id


def test_legacy_conversation_id_containing_colons_upgrades(db):
    c=contact(db,external='0:1:123:456',conversation_type='group'); t=task(db,contact=c)
    item=snapshot_item(db,t,t.recipients[0])
    sync_contacts(db,'a','u',[raw('renamed','conv:0:1:123:456',conversation_id='0:1:123:456',conversation_type='group')])
    assert len(db.scalars(select(Contact)).all())==1 and t.recipients[0].contact_id==c.id
    assert resolve_run_contact(db,t,item).external_id=='conv:0:1:123:456'


@pytest.mark.parametrize('metadata,expected,want', [
    ({'conversation_id':'1'},'conv:1',True), ({'uid':'1'},'conv:1',False),
    ({'sec_uid':'s'},'sec:s',True), ({'uid':'1'},'uid:1',True),
    ({'douyin_id':'handle'},'handle',False), ({'uid':'1'},'1',True), ({},None,False)])
def test_typed_identity_matching(metadata,expected,want):
    assert identity_matches(metadata,expected) is want


def test_no_id_never_opens_browser_or_sends():
    page=Mock()
    assert douyin._send_one(page,'name','test',None).status=='needs_selection'
    page.locator.assert_not_called()
    assert douyin.send_batch('{}',[]) == ([],None,'{}')


def test_no_nickname_fallback_to_imposter():
    page=Mock()
    title=Mock(); title.is_visible.return_value=True; title.inner_text.return_value='old'
    title.evaluate.return_value={'conversation_id':'imposter'}
    page.locator.return_value.count.return_value=1
    page.locator.return_value.nth.return_value=title
    assert douyin._find_conversation_title(page,'old','conv:one') is None
    title.click.assert_not_called()


def test_selected_conversation_must_match():
    page=Mock(); page.locator.return_value.count.return_value=1
    page.locator.return_value.first.evaluate.return_value={'conversation_id':'other'}
    assert not douyin._verify_conversation(page,'conv:one')
    page.locator.return_value.first.evaluate.return_value={'conversation_id':'one'}
    assert douyin._verify_conversation(page,'conv:one')


def test_profile_handle_is_search_metadata_only():
    collector=ProfileCollector(Mock()); response=Mock()
    response.url='https://www.douyin.com/aweme/v1/web/user/profile/'
    response.headers={'content-type':'application/json'}
    response.body.return_value=json.dumps({'user':{'uid':'1','sec_uid':'s','unique_id':'mutable','nickname':'new'}}).encode()
    collector.observe(response)
    rows=[{'uid':'1','external_id':'conv:one'}]; collector.enrich(rows)
    assert rows[0]['douyin_id']=='mutable' and rows[0]['external_id']=='conv:one'


def test_im_profile_response_without_json_content_type_is_collected():
    collector=ProfileCollector(Mock()); response=Mock()
    response.url='https://www.douyin.com/aweme/v1/web/im/user/info/'
    response.headers={'content-type':'text/plain'}
    response.body.return_value=json.dumps({'data':[{'uid':'1','sec_uid':'s','nickname':'new','remark_name':'','unique_id':'handle'}]}).encode()
    collector.observe(response)
    rows=[{'uid':'1','remark_name':'old','external_id':'sec:s'}]; collector.enrich(rows)
    assert rows[0]['douyin_id']=='handle' and rows[0]['remark_name'] is None
    assert rows[0]['external_id']=='sec:s'


def test_clear_remark_updates_metadata_without_unbinding(db):
    c=contact(db,remark_name='old remark'); t=task(db,contact=c)
    sync_contacts(db,'a','u',[raw('nickname',remark_name=None,_profile_fields=['remark_name'])])
    assert c.remark_name is None and t.recipients[0].contact_id==c.id


def test_schema_migration_retains_rows_and_allows_same_names():
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY)')
        c.exec_driver_sql('CREATE TABLE task_recipients (id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36), contact_id VARCHAR(36), contact_name VARCHAR(120), conversation_type VARCHAR(16), message VARCHAR(500), enabled BOOLEAN, UNIQUE(task_id, contact_name))')
        c.exec_driver_sql("INSERT INTO tasks VALUES ('t')")
        c.exec_driver_sql("INSERT INTO task_recipients VALUES ('r','t','c','same','friend','test',1)")
        c.exec_driver_sql('CREATE TABLE contacts (id VARCHAR(36))')
        c.exec_driver_sql('CREATE TABLE run_items (id VARCHAR(36))')
    migrate_identity_schema(engine); migrate_identity_schema(engine)
    with engine.begin() as c:
        assert c.exec_driver_sql('SELECT count(*) FROM task_recipients').scalar()==1
        c.exec_driver_sql("INSERT INTO task_recipients VALUES ('r2','t','c2','same','friend','test',1)")
        assert c.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
    assert 'external_id' in {c['name'] for c in inspect(engine).get_columns('run_items')}


def test_real_browser_model_getters_and_current_identity():
    from playwright.sync_api import Error, sync_playwright
    with sync_playwright() as p:
        try:
            browser=p.chromium.launch(headless=True,executable_path=os.environ.get('TEST_BROWSER_EXECUTABLE'),args=['--no-sandbox','--disable-dev-shm-usage'])
        except Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip('Install Playwright Chromium or set TEST_BROWSER_EXECUTABLE to an existing Chrome')
            raise
        page=browser.new_page()
        page.set_content('<div data-e2e="conversation-item" class="conversationConversationItemcurConversation"><span class="conversationConversationItemtitle">changed remark</span></div>')
        page.evaluate('''() => {
            const row=document.querySelector('[data-e2e="conversation-item"]');
            const conv={id:'conversation',user:{unique_id:'handle',nickname:'nickname'}};
            Object.defineProperty(conv,'toParticipantUserId',{get:()=> 'uid'});
            Object.defineProperty(conv,'toParticipantSecUserId',{get:()=> 'sec'});
            row.__reactFiber$test={memoizedProps:{conversation:conv}};
        }''')
        metadata=page.locator('.conversationConversationItemtitle').evaluate(IDENTITY_READER)
        assert metadata['uid']=='uid' and metadata['sec_uid']=='sec'
        assert metadata['external_id']=='conv:conversation' and metadata['douyin_id']=='handle'
        assert douyin._verify_conversation(page,'conv:conversation')
        assert not douyin._verify_conversation(page,'conv:wrong')
        browser.close()


def test_execute_run_routes_same_names_by_item_id(db, monkeypatch):
    c1=contact(db,'same'); c2=contact(db,'same','conv:two'); t=task(db,'same',c1)
    t.recipients.append(TaskRecipient(contact_name='same',contact_id=c2.id,message='test2')); db.flush()
    run=Run(task_id=t.id,user_id='u',scheduled_for=utcnow(),status='running',attempt=1)
    run.items=[snapshot_item(db,t,r) for r in t.recipients]; db.add(run); db.commit()
    engine=db.get_bind()
    monkeypatch.setattr(worker,'SessionLocal',lambda: Session(engine,expire_on_commit=False))
    monkeypatch.setattr(worker,'decrypt_text',lambda _: '{}')
    monkeypatch.setattr(worker,'encrypt_text',lambda _: 'dummy')
    seen=[]
    def send(state, recipients, before, result, **kwargs):
        results=[]
        for name,message,identity,item_id in recipients:
            seen.append((identity,item_id)); before(item_id)
            item=douyin.SendResult(name,'submitted',item_id=item_id); result(item); results.append(item)
        return results,None,'{}'
    monkeypatch.setattr(worker,'send_batch',send)
    worker.execute_run(run.id); db.expire_all()
    assert db.get(Run,run.id).status=='success'
    assert {p[0] for p in seen}=={'conv:one','conv:two'}
    assert len({p[1] for p in seen})==2
    assert all(i.status=='submitted' for i in db.get(Run,run.id).items)


def test_execute_run_missing_identity_holds_without_retry(db, monkeypatch):
    c=contact(db,external=None); t=task(db,contact=c)
    run=Run(task_id=t.id,user_id='u',scheduled_for=utcnow(),status='running',attempt=1)
    run.items=[snapshot_item(db,t,t.recipients[0])]; db.add(run); db.commit()
    engine=db.get_bind()
    monkeypatch.setattr(worker,'SessionLocal',lambda: Session(engine,expire_on_commit=False))
    monkeypatch.setattr(worker,'decrypt_text',lambda _: '{}')
    monkeypatch.setattr(worker,'encrypt_text',lambda _: 'dummy')
    def no_send(state, recipients, *args, **kwargs):
        assert not recipients
        return [],None,''
    monkeypatch.setattr(worker,'send_batch',no_send)
    worker.execute_run(run.id); db.expire_all()
    assert db.get(Run,run.id).error_code=='CONTACT_IDENTITY_REQUIRED'
    assert db.get(Run,run.id).items[0].status=='needs_selection'
    assert db.scalar(select(Run).where(Run.parent_run_id==run.id)) is None


def test_execute_retry_survives_plan_edit_and_rename(db, monkeypatch):
    c=contact(db); t=task(db,contact=c)
    run=Run(task_id=t.id,user_id='u',scheduled_for=utcnow(),attempt=2,status='running')
    run.items=[snapshot_item(db,t,t.recipients[0])]; db.add(run); db.commit()
    t.recipients.clear(); c.display_name='changed'; db.commit()
    engine=db.get_bind()
    monkeypatch.setattr(worker,'SessionLocal',lambda: Session(engine,expire_on_commit=False))
    monkeypatch.setattr(worker,'decrypt_text',lambda _: '{}')
    monkeypatch.setattr(worker,'encrypt_text',lambda _: 'dummy')
    def send(state, recipients, before, result, **kwargs):
        assert recipients[0][0]=='changed' and recipients[0][2]=='conv:one'
        before(recipients[0][3]); item=douyin.SendResult('changed','submitted',item_id=recipients[0][3]); result(item)
        return [item],None,''
    monkeypatch.setattr(worker,'send_batch',send)
    worker.execute_run(run.id); db.expire_all()
    assert db.get(Run,run.id).status=='success'


def test_new_plan_requires_stable_contact_id_and_accepts_same_names(db):
    from app.main import _task_recipients
    from app.schemas import TaskCreate
    from fastapi import HTTPException
    c1=contact(db,'same'); c2=contact(db,'same','conv:two')
    account=db.get(DouyinAccount,'a'); user=db.get(User,'u')
    payload=TaskCreate(account_id='a',name='plan',send_time='00:00',recipients=[
        {'contact_id':c1.id,'contact_name':'same','message':'test'},
        {'contact_id':c2.id,'contact_name':'same','message':'test'}])
    assert len(_task_recipients(payload,account,user,db))==2
    payload.recipients[0].contact_id=None
    with pytest.raises(HTTPException,match='重新选择'):
        _task_recipients(payload,account,user,db)


def test_send_batch_callbacks_use_item_id_without_real_browser(monkeypatch):
    playwright=Mock(); browser=playwright.chromium.launch.return_value
    context=browser.new_context.return_value; context.storage_state.return_value={}
    page=context.new_page.return_value
    manager=Mock(); manager.__enter__=Mock(return_value=playwright); manager.__exit__=Mock(return_value=False)
    monkeypatch.setattr(douyin,'sync_playwright',lambda:manager)
    monkeypatch.setattr(douyin,'_context_page',lambda _:page)
    monkeypatch.setattr(douyin,'login_state',lambda _: (True,''))
    monkeypatch.setattr(douyin,'_send_one',lambda p,n,m,e:douyin.SendResult(n,'submitted'))
    tokens=[]; results=[]
    rows=[('same','test','conv:one','item1'),('same','test','conv:two','item2')]
    output,fatal,state=douyin.send_batch('{}',rows,tokens.append,results.append)
    assert tokens==['item1','item2']
    assert [r.item_id for r in results]==['item1','item2'] and fatal is None
