"""Idempotent SQLite migration; display names are no longer unique identities."""
from sqlalchemy import inspect


def migrate_identity_schema(engine):
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    with engine.begin() as connection:
        if "task_recipients" in tables:
            columns = {c["name"] for c in inspector.get_columns("task_recipients")}
            if "contact_id" not in columns:
                connection.exec_driver_sql("ALTER TABLE task_recipients ADD COLUMN contact_id VARCHAR(36)")
            if "conversation_type" not in columns:
                connection.exec_driver_sql("ALTER TABLE task_recipients ADD COLUMN conversation_type VARCHAR(16) DEFAULT 'unknown' NOT NULL")
        for table, fields in {
            "contacts": {**{f: "VARCHAR(240)" for f in ("uid", "sec_uid", "conversation_id", "douyin_id", "nickname", "remark_name")}, "identity_conflict": "BOOLEAN DEFAULT 0 NOT NULL"},
            "run_items": {"recipient_id": "VARCHAR(36)", "contact_id": "VARCHAR(36)", "external_id": "VARCHAR(240)"},
        }.items():
            if table not in tables:
                continue
            columns = {c["name"] for c in inspector.get_columns(table)}
            for field, sql_type in fields.items():
                if field not in columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {field} {sql_type}")
        if "task_recipients" in tables and any(c.get("column_names") == ["task_id", "contact_name"] for c in inspector.get_unique_constraints("task_recipients")):
            if engine.dialect.name != "sqlite":
                raise RuntimeError("Identity migration requires SQLite")
            connection.exec_driver_sql('''CREATE TABLE task_recipients_identity (
                id VARCHAR(36) PRIMARY KEY NOT NULL,
                task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                contact_id VARCHAR(36), contact_name VARCHAR(120) NOT NULL,
                conversation_type VARCHAR(16) NOT NULL DEFAULT 'unknown',
                message VARCHAR(500) NOT NULL, enabled BOOLEAN NOT NULL,
                CONSTRAINT uq_task_contact_identity UNIQUE(task_id, contact_id))''')
            connection.exec_driver_sql('''INSERT INTO task_recipients_identity
                SELECT id, task_id, contact_id, contact_name, conversation_type, message, enabled FROM task_recipients''')
            connection.exec_driver_sql("DROP TABLE task_recipients")
            connection.exec_driver_sql("ALTER TABLE task_recipients_identity RENAME TO task_recipients")
            connection.exec_driver_sql("CREATE INDEX ix_task_recipients_task_id ON task_recipients (task_id)")
