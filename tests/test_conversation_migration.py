from __future__ import annotations

import secrets
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from knowledge_rag.conversation_store import ConversationStore
from knowledge_rag.db import (
    ConversationRecord,
    ConversationTurnRecord,
    KnowledgeBaseRecord,
    UserKnowledgeBaseRecord,
    UserRecord,
    build_engine,
)
from knowledge_rag.security import hash_password


class ConversationMigrationTests(unittest.TestCase):
    def test_upgrade_downgrade_preserves_existing_accounts_and_cascades_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory) / 'migration.db'}"
            with patch.dict("os.environ", {"DATABASE_URL": url}):
                config = Config("alembic.ini")
                command.upgrade(config, "5af16c204e9b")
                engine = build_engine(url)
                self.addCleanup(engine.dispose)
                now = datetime.now(UTC).isoformat()
                with Session(engine) as session, session.begin():
                    session.add(UserRecord(id="migration-reader", username="migration-reader", display_name="Migration reader", password_hash=hash_password(secrets.token_urlsafe(32)), role="user", active=True, created_at=now, updated_at=now))
                    session.add(KnowledgeBaseRecord(id="default", name="Temporary knowledge base", description="", active=True, created_at=now, updated_at=now))
                with Session(engine) as session, session.begin():
                    session.add(UserKnowledgeBaseRecord(user_id="migration-reader", knowledge_base_id="default", created_at=now))
                command.upgrade(config, "head")
                self.assertIn("conversation_turns", inspect(engine).get_table_names())
                history = ConversationStore(engine)
                conversation = history.create("migration-reader", "default", "Migration check")
                history.append("migration-reader", conversation.id, "question", "answer", False, "en", [])
                self.assertEqual(history.get("migration-reader", conversation.id)[1][0].answer, "answer")
                command.downgrade(config, "5af16c204e9b")
                self.assertNotIn("conversations", inspect(engine).get_table_names())
                with Session(engine) as session:
                    self.assertEqual(session.scalar(select(func.count(UserRecord.id))), 1)
                    self.assertEqual(session.scalar(select(func.count(KnowledgeBaseRecord.id))), 1)
                command.upgrade(config, "head")
                conversation = history.create("migration-reader", "default", "Cascade check")
                history.append("migration-reader", conversation.id, "question", "answer", False, "en", [])
                with Session(engine) as session, session.begin():
                    session.delete(session.get(UserRecord, "migration-reader"))
                with Session(engine) as session:
                    self.assertEqual(session.scalar(select(func.count(ConversationRecord.id))), 0)
                    self.assertEqual(session.scalar(select(func.count(ConversationTurnRecord.id))), 0)
