from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from knowledge_rag.db import (
    ConversationRecord,
    ConversationTurnRecord,
    KnowledgeBaseRecord,
    UserKnowledgeBaseRecord,
    UserRecord,
)

MAX_CONVERSATION_TURNS = 200


@dataclass(frozen=True)
class Conversation:
    id: str
    knowledge_base_id: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int


@dataclass(frozen=True)
class ConversationTurn:
    id: int
    question: str
    answer: str
    grounded: bool
    locale: str
    sources: list[dict[str, Any]]
    created_at: str


class ConversationStore:
    """Account-owned history; current grants are checked on every read and append."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _visible(self, user_id: str) -> Select[tuple[ConversationRecord]]:
        grant = exists().where(
            UserKnowledgeBaseRecord.user_id == user_id,
            UserKnowledgeBaseRecord.knowledge_base_id == ConversationRecord.knowledge_base_id,
        )
        return select(ConversationRecord).join(UserRecord, UserRecord.id == ConversationRecord.user_id).join(
            KnowledgeBaseRecord, KnowledgeBaseRecord.id == ConversationRecord.knowledge_base_id,
        ).where(
            ConversationRecord.user_id == user_id,
            UserRecord.active.is_(True), KnowledgeBaseRecord.active.is_(True),
            or_(UserRecord.role == "admin", grant),
        )

    @staticmethod
    def _summary(row: ConversationRecord, count: int) -> Conversation:
        return Conversation(row.id, row.knowledge_base_id, row.title, row.created_at, row.updated_at, count)

    def _owned(self, session: Session, user_id: str, conversation_id: str) -> ConversationRecord:
        row = session.scalar(self._visible(user_id).where(ConversationRecord.id == conversation_id))
        if row is None:
            raise KeyError(conversation_id)
        return row

    def create(self, user_id: str, knowledge_base_id: str, title: str) -> Conversation:
        title = " ".join(title.split())
        if not title or len(title) > 100:
            raise ValueError("会话标题须为1至100个字符")
        with Session(self.engine) as session, session.begin():
            grant = exists().where(UserKnowledgeBaseRecord.user_id == user_id, UserKnowledgeBaseRecord.knowledge_base_id == knowledge_base_id)
            allowed_user = select(UserRecord.id).where(
                UserRecord.id == user_id, UserRecord.active.is_(True),
                or_(UserRecord.role == "admin", grant),
                exists().where(KnowledgeBaseRecord.id == knowledge_base_id, KnowledgeBaseRecord.active.is_(True)),
            )
            if session.scalar(allowed_user) is None:
                raise PermissionError("没有访问该知识库的权限")
            now = datetime.now(UTC).isoformat()
            row = ConversationRecord(id=str(uuid4()), user_id=user_id, knowledge_base_id=knowledge_base_id, title=title, created_at=now, updated_at=now)
            session.add(row)
            return self._summary(row, 0)

    def list(self, user_id: str, knowledge_base_id: str | None = None, limit: int = 30, offset: int = 0) -> tuple[list[Conversation], bool]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid history pagination")
        count = select(func.count(ConversationTurnRecord.id)).where(ConversationTurnRecord.conversation_id == ConversationRecord.id).scalar_subquery()
        statement = self._visible(user_id).add_columns(count).order_by(ConversationRecord.updated_at.desc(), ConversationRecord.id.desc()).offset(offset).limit(limit + 1)
        if knowledge_base_id is not None:
            statement = statement.where(ConversationRecord.knowledge_base_id == knowledge_base_id)
        with Session(self.engine) as session:
            rows = session.execute(statement).all()
            return [self._summary(row, total) for row, total in rows[:limit]], len(rows) > limit

    def inspect(self, user_id: str, conversation_id: str) -> Conversation:
        """Authorize and read metadata without decoding every historical source snapshot."""
        with Session(self.engine) as session:
            row = self._owned(session, user_id, conversation_id)
            count = session.scalar(select(func.count(ConversationTurnRecord.id)).where(ConversationTurnRecord.conversation_id == row.id)) or 0
            return self._summary(row, count)

    def get(self, user_id: str, conversation_id: str) -> tuple[Conversation, list[ConversationTurn]]:
        with Session(self.engine) as session:
            row = self._owned(session, user_id, conversation_id)
            records = session.scalars(select(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == row.id).order_by(ConversationTurnRecord.id)).all()
            turns = []
            for record in records:
                try:
                    sources = json.loads(record.sources_json)
                    if not isinstance(sources, list) or any(not isinstance(item, dict) for item in sources):
                        raise ValueError("invalid source snapshot")
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("保存的会话引用不是有效数据") from exc
                turns.append(ConversationTurn(record.id, record.question, record.answer, record.grounded, record.locale, sources, record.created_at))
            return self._summary(row, len(turns)), turns

    def append(self, user_id: str, conversation_id: str, question: str, answer: str, grounded: bool, locale: str, sources: list[dict[str, Any]]) -> None:
        encoded = json.dumps(sources, ensure_ascii=False, allow_nan=False)
        with Session(self.engine) as session, session.begin():
            # Grant replacement and account status writes lock this same user row.
            # A no-op write also starts SQLite's write transaction before any reads;
            # unlike an in-process mutex or SELECT FOR UPDATE alone it works there.
            session.execute(update(UserRecord).where(UserRecord.id == user_id).values(active=UserRecord.active))
            knowledge_base_id = session.scalar(select(ConversationRecord.knowledge_base_id).where(ConversationRecord.id == conversation_id, ConversationRecord.user_id == user_id))
            if knowledge_base_id is None:
                raise KeyError(conversation_id)
            # PostgreSQL SHARE allows other readers/appends but blocks KB state
            # updates/deletion. SQLite is already protected by the write transaction.
            session.execute(select(KnowledgeBaseRecord.id).where(KnowledgeBaseRecord.id == knowledge_base_id).with_for_update(read=True))
            self._owned(session, user_id, conversation_id)
            now = datetime.now(UTC).isoformat()
            # Updating the parent serializes appends and prevents concurrent deletion.
            changed = session.scalar(update(ConversationRecord).where(ConversationRecord.id == conversation_id, ConversationRecord.user_id == user_id).values(updated_at=now).returning(ConversationRecord.id))
            if changed is None:
                raise KeyError(conversation_id)
            count = session.scalar(select(func.count(ConversationTurnRecord.id)).where(ConversationTurnRecord.conversation_id == conversation_id)) or 0
            if count >= MAX_CONVERSATION_TURNS:
                raise ValueError("会话已达到200轮，请新建会话")
            session.add(ConversationTurnRecord(conversation_id=conversation_id, question=question, answer=answer, grounded=grounded, locale=locale, sources_json=encoded, created_at=now))

    def delete(self, user_id: str, conversation_id: str) -> None:
        # Owners may erase their history even after the knowledge-base grant is revoked.
        with Session(self.engine) as session, session.begin():
            changed = session.scalar(delete(ConversationRecord).where(ConversationRecord.id == conversation_id, ConversationRecord.user_id == user_id).returning(ConversationRecord.id))
            if changed is None:
                raise KeyError(conversation_id)
