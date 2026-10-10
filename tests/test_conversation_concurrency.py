from __future__ import annotations

import os
import secrets
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.api import create_app
from knowledge_rag.config import Settings
from knowledge_rag.db import Base, ConversationTurnRecord, KnowledgeBaseRecord
from knowledge_rag.rag import ChatResult


class ConversationAuthorizationConcurrencyTests(unittest.TestCase):
    database_url = ""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = replace(Settings(), app_db_path=Path(directory.name) / "app.db", data_dir=Path(directory.name) / "data", database_url=self.database_url, model_secret_key=secrets.token_urlsafe(24), auth_required=True)
        if self.database_url:
            url = make_url(self.database_url)
            if url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost"} or not (url.database or "").startswith("knowledge_history_qa_"):
                raise ValueError("PostgreSQL concurrency tests require a dedicated local knowledge_history_qa_ database")
        database = self.database_url or self.settings.app_db_path
        self.store = AdminStore(database, self.settings.model_secret_key)
        self.addCleanup(self.store.engine.dispose)
        if self.database_url:
            self.addCleanup(Base.metadata.drop_all, self.store.engine)
        self.store.seed_defaults(self.settings)
        self.password = secrets.token_urlsafe(24)
        self.reader = self.store.create_user("qa_reader", "QA Reader", self.password, "user")
        self.admin = self.store.create_user("qa_admin", "QA Admin", secrets.token_urlsafe(24), "admin")
        # An independent Engine/connection, like another API worker; never an RLock.
        self.mutator = AdminStore(database, self.settings.model_secret_key)
        self.addCleanup(self.mutator.engine.dispose)
        self.history = create_app(settings=self.settings, admin_store=self.store)
        self.client = TestClient(self.history)
        self.addCleanup(self.client.close)
        self.conversations = self.history.state.conversation_store

        def bounded_writer(connection: Connection) -> None:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA busy_timeout=0")
            else:
                connection.exec_driver_sql("SET LOCAL lock_timeout='100ms'")

        event.listen(self.mutator.engine, "begin", bounded_writer)

    def test_mutation_and_append_are_serialized_at_both_write_boundaries(self) -> None:
        for boundary in ("after_authorization", "after_parent_update"):
            for change in ("grant", "user_active", "knowledge_base_active"):
                with self.subTest(boundary=boundary, change=change):
                    self._assert_interleaving(boundary, change)

    def _assert_interleaving(self, boundary: str, change: str) -> None:
            self.store.set_user_active(self.reader.id, True, self.admin.id)
            with Session(self.store.engine) as session, session.begin():
                session.execute(update(KnowledgeBaseRecord).where(KnowledgeBaseRecord.id == "default").values(active=True))
            self.store.set_user_knowledge_bases(self.reader.id, ["default"], self.admin.id)
            self.assertEqual(self.client.post("/api/v1/auth/login", json={"username": "qa_reader", "password": self.password}).status_code, 200)
            created = self.client.post("/api/v1/conversations", json={"title": "Race probe"})
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()["id"]
            outcome: list[str] = []

            def mutate() -> None:
                if change == "grant":
                    self.mutator.set_user_knowledge_bases(self.reader.id, [], self.admin.id)
                elif change == "user_active":
                    self.mutator.set_user_active(self.reader.id, False, self.admin.id)
                else:
                    with Session(self.mutator.engine) as session, session.begin():
                        session.execute(update(KnowledgeBaseRecord).where(KnowledgeBaseRecord.id == "default").values(active=False))

            def attempt_mutation() -> None:
                self.assertFalse(outcome, "each interleaving must be exercised once")
                try:
                    mutate()
                except OperationalError as exc:
                    code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "sqlite_errorcode", None)
                    self.assertIn(code, ("55P03", 5), str(exc.orig))
                    outcome.append("blocked")
                else:
                    outcome.append("committed")

            original_append = self.conversations.append
            original_owned = self.conversations._owned

            def checked(*args: Any) -> Any:
                row = original_owned(*args)
                attempt_mutation()
                return row

            def after_update(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
                if statement.lstrip().upper().startswith("UPDATE CONVERSATIONS "):
                    attempt_mutation()

            def append(*args: Any) -> None:
                if boundary == "after_authorization":
                    with patch.object(self.conversations, "_owned", side_effect=checked):
                        original_append(*args)
                else:
                    event.listen(self.store.engine, "after_cursor_execute", after_update)
                    try:
                        original_append(*args)
                    finally:
                        event.remove(self.store.engine, "after_cursor_execute", after_update)

            with patch("knowledge_rag.api.get_rag_service") as service, patch.object(self.conversations, "append", side_effect=append):
                service.return_value.answer.return_value = ChatResult("PRIVATE RACE ANSWER", False, ())
                response = self.client.post("/api/v1/chat", json={"question": "race", "conversation_id": identifier})
            self.assertEqual(len(outcome), 1)
            with Session(self.store.engine) as session:
                count = session.scalar(select(func.count(ConversationTurnRecord.id)).where(ConversationTurnRecord.conversation_id == identifier))
            if outcome == ["committed"]:
                # A committed revocation must invalidate the in-flight append.
                self.assertEqual(response.status_code, 404, response.text)
                self.assertNotIn("PRIVATE RACE ANSWER", response.text)
                self.assertEqual(count, 0)
            else:
                # Save won the database lock; revocation is ordered after commit.
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(count, 1)
                mutate()
            self.assertEqual(self.client.get(f"/api/v1/conversations/{identifier}").status_code, 401 if change == "user_active" else 404)


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_POSTGRES_URL"), "dedicated PostgreSQL test URL not configured")
class PostgresConversationAuthorizationConcurrencyTests(ConversationAuthorizationConcurrencyTests):
    database_url = os.getenv("KNOWLEDGE_TEST_POSTGRES_URL", "")
