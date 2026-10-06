from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from knowledge_rag.config import Settings
from knowledge_rag.db import (
    AppSettingRecord,
    AuditLogRecord,
    Base,
    IndexJobRecord,
    KnowledgeBaseRecord,
    ModelProfileRecord,
    SessionRecord,
    UserKnowledgeBaseRecord,
    UserPreferenceRecord,
    UserRecord,
    build_engine,
    migrate_legacy_sqlite_model_profiles,
)
from knowledge_rag.security import (
    SecretCipher,
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str
    role: str
    active: bool


@dataclass(frozen=True)
class KnowledgeBase:
    id: str
    name: str
    description: str
    active: bool


@dataclass(frozen=True)
class ModelProfile:
    id: str
    name: str
    purpose: str
    protocol: str
    base_url: str
    api_key: str
    model_name: str
    vector_dim: int | None
    options: dict[str, Any]
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IndexJob:
    id: str
    profile_id: str
    knowledge_base_id: str
    collection_name: str
    status: str
    total_chunks: int
    completed_chunks: int
    error: str
    created_at: str
    updated_at: str


class AdminStore:
    """Transactional business store supporting SQLite locally and PostgreSQL in production."""

    def __init__(self, database: str | Path, secret_key: str = "") -> None:
        if isinstance(database, Path) or "://" not in str(database):
            path = Path(database).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{path}"
        else:
            database_url = str(database)
        self.engine: Engine = build_engine(database_url)
        self.cipher = SecretCipher(secret_key)
        migrate_legacy_sqlite_model_profiles(self.engine)
        Base.metadata.create_all(self.engine)
        self._initialize()

    def _initialize(self) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(delete(SessionRecord).where(SessionRecord.expires_at <= _now()))
            session.execute(
                update(IndexJobRecord)
                .where(IndexJobRecord.status.in_(["queued", "running"]))
                .values(status="failed", error="服务重启导致任务中断", updated_at=_now())
            )

    def seed_defaults(self, settings: Settings) -> None:
        now = _now()
        with Session(self.engine) as session, session.begin():
            if session.get(KnowledgeBaseRecord, "default") is None:
                session.add(
                    KnowledgeBaseRecord(
                        id="default",
                        name="默认知识库",
                        description="由管理员维护的私有文档知识库",
                        active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
            profile_count = session.scalar(select(func.count()).select_from(ModelProfileRecord)) or 0
            if profile_count:
                return
            chat_id = str(uuid4())
            embedding_id = str(uuid4())
            session.add_all(
                [
                    ModelProfileRecord(
                        id=chat_id,
                        name="本地 Qwen 对话模型",
                        purpose="chat",
                        protocol="ollama",
                        base_url=settings.ollama_base_url,
                        model_name=settings.ollama_chat_model,
                        vector_dim=None,
                        options_json=json.dumps({"keep_alive": settings.ollama_keep_alive}),
                        created_at=now,
                        updated_at=now,
                    ),
                    ModelProfileRecord(
                        id=embedding_id,
                        name="本地 Qwen Embedding",
                        purpose="embedding",
                        protocol="ollama",
                        base_url=settings.ollama_base_url,
                        model_name=settings.ollama_embed_model,
                        vector_dim=settings.milvus_vector_dim,
                        options_json=json.dumps({"keep_alive": settings.ollama_keep_alive}),
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            settings_values = {
                "active_chat_profile_id": chat_id,
                "active_embedding_profile_id": embedding_id,
                "active_collection_name": settings.milvus_collection,
                "active_vector_dim": str(settings.milvus_vector_dim),
                "active_reranker_profile_id": "",
            }
            session.add_all([AppSettingRecord(key=key, value=value, updated_at=now) for key, value in settings_values.items()])

    def ensure_bootstrap_admin(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        with Session(self.engine) as session, session.begin():
            if session.scalar(select(func.count()).select_from(UserRecord)):
                return False
            self._insert_user(session, username, username, password, "admin")
            return True

    def _insert_user(self, session: Session, username: str, display_name: str, password: str, role: str) -> User:
        if role not in {"admin", "user"}:
            raise ValueError("无效角色")
        normalized_username = username.strip().lower()
        if not normalized_username:
            raise ValueError("用户名不能为空")
        user = User(str(uuid4()), normalized_username, display_name.strip() or normalized_username, role, True)
        now = _now()
        session.add(
            UserRecord(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                password_hash=hash_password(password),
                role=user.role,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        return user

    def create_user(self, username: str, display_name: str, password: str, role: str, actor_id: str | None = None) -> User:
        with Session(self.engine) as session, session.begin():
            user = self._insert_user(session, username, display_name, password, role)
            self._audit(session, actor_id, "user.create", "user", user.id, {"username": user.username, "role": role})
            return user

    def list_users(self) -> list[User]:
        with Session(self.engine) as session:
            rows = session.scalars(select(UserRecord).order_by(UserRecord.created_at)).all()
            return [self._user(row) for row in rows]

    def get_user(self, user_id: str) -> User | None:
        with Session(self.engine) as session:
            row = session.get(UserRecord, user_id)
            return self._user(row) if row else None

    def get_user_locale(self, user_id: str) -> Literal["zh-CN", "en"]:
        with Session(self.engine) as session:
            record = session.get(UserPreferenceRecord, user_id)
            return "en" if record and record.preferred_locale == "en" else "zh-CN"

    def set_user_locale(self, user_id: str, locale: str) -> str:
        if locale not in {"zh-CN", "en"}:
            raise ValueError("不支持的显示语言")
        with Session(self.engine) as session, session.begin():
            if session.get(UserRecord, user_id) is None:
                raise KeyError(user_id)
            record = session.get(UserPreferenceRecord, user_id)
            if record is None:
                session.add(UserPreferenceRecord(user_id=user_id, preferred_locale=locale, updated_at=_now()))
            else:
                record.preferred_locale = locale
                record.updated_at = _now()
            self._audit(session, user_id, "user.locale", "user", user_id, {"locale": locale})
        return locale

    def update_display_name(self, user_id: str, display_name: str) -> User:
        name = display_name.strip()
        if not name or len(name) > 100:
            raise ValueError("显示名称须为 1 到 100 个字符")
        with Session(self.engine) as session, session.begin():
            row = session.get(UserRecord, user_id)
            if row is None:
                raise KeyError(user_id)
            row.display_name = name
            row.updated_at = _now()
            self._audit(session, user_id, "user.display_name", "user", user_id, {})
            return self._user(row)

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        if len(new_password) < 8 or len(new_password) > 128:
            raise ValueError("新密码须为 8 到 128 个字符")
        with Session(self.engine) as session, session.begin():
            row = session.get(UserRecord, user_id)
            if row is None or not verify_password(current_password, row.password_hash):
                raise ValueError("当前密码错误")
            if current_password == new_password:
                raise ValueError("新密码不能与当前密码相同")
            row.password_hash = hash_password(new_password)
            row.updated_at = _now()
            session.execute(delete(SessionRecord).where(SessionRecord.user_id == user_id))
            self._audit(session, user_id, "user.password", "user", user_id, {})

    def authenticate(self, username: str, password: str) -> User | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(UserRecord).where(func.lower(UserRecord.username) == username.strip().lower(), UserRecord.active.is_(True))
            )
            if row is None or not verify_password(password, row.password_hash):
                return None
            return self._user(row)

    def create_session(self, user_id: str, hours: int) -> str:
        token = new_session_token()
        now = datetime.now(UTC)
        with Session(self.engine) as session, session.begin():
            session.add(
                SessionRecord(
                    token_hash=hash_session_token(token),
                    user_id=user_id,
                    expires_at=(now + timedelta(hours=hours)).isoformat(),
                    created_at=now.isoformat(),
                )
            )
        return token

    def get_session_user(self, token: str) -> User | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(UserRecord)
                .join(SessionRecord, SessionRecord.user_id == UserRecord.id)
                .where(
                    SessionRecord.token_hash == hash_session_token(token),
                    SessionRecord.expires_at > _now(),
                    UserRecord.active.is_(True),
                )
            )
            return self._user(row) if row else None

    def delete_session(self, token: str) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(delete(SessionRecord).where(SessionRecord.token_hash == hash_session_token(token)))

    def set_user_active(self, user_id: str, active: bool, actor_id: str) -> User:
        if user_id == actor_id and not active:
            raise ValueError("不能禁用当前登录的管理员账户")
        with Session(self.engine) as session, session.begin():
            row = session.get(UserRecord, user_id)
            if row is None:
                raise KeyError(user_id)
            row.active = active
            row.updated_at = _now()
            if not active:
                session.execute(delete(SessionRecord).where(SessionRecord.user_id == user_id))
            self._audit(session, actor_id, "user.status", "user", user_id, {"active": active})
            result = self._user(row)
        return result

    def reset_user_password(self, user_id: str, password: str, actor_id: str) -> None:
        new_hash = hash_password(password)
        with Session(self.engine) as session, session.begin():
            row = session.get(UserRecord, user_id)
            if row is None:
                raise KeyError(user_id)
            row.password_hash = new_hash
            row.updated_at = _now()
            session.execute(delete(SessionRecord).where(SessionRecord.user_id == user_id))
            self._audit(session, actor_id, "user.password_reset", "user", user_id, {})

    def list_knowledge_bases(self, user: User | None = None) -> list[KnowledgeBase]:
        with Session(self.engine) as session:
            statement = select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.active.is_(True)).order_by(KnowledgeBaseRecord.name)
            if user is not None and user.role != "admin":
                statement = statement.join(
                    UserKnowledgeBaseRecord,
                    UserKnowledgeBaseRecord.knowledge_base_id == KnowledgeBaseRecord.id,
                ).where(UserKnowledgeBaseRecord.user_id == user.id)
            rows = session.scalars(statement).all()
            return [self._knowledge_base(row) for row in rows]

    def user_knowledge_base_ids(self, user_id: str) -> list[str]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(UserKnowledgeBaseRecord.knowledge_base_id)
                    .join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == UserKnowledgeBaseRecord.knowledge_base_id)
                    .where(UserKnowledgeBaseRecord.user_id == user_id, KnowledgeBaseRecord.active.is_(True))
                    .order_by(UserKnowledgeBaseRecord.knowledge_base_id)
                ).all()
            )

    def set_user_knowledge_bases(self, user_id: str, knowledge_base_ids: list[str], actor_id: str) -> list[str]:
        unique_ids = sorted(set(knowledge_base_ids))
        with Session(self.engine) as session, session.begin():
            if session.get(UserRecord, user_id) is None:
                raise KeyError(user_id)
            if unique_ids:
                existing = set(
                    session.scalars(select(KnowledgeBaseRecord.id).where(KnowledgeBaseRecord.id.in_(unique_ids))).all()
                )
                missing = set(unique_ids) - existing
                if missing:
                    raise ValueError(f"知识库不存在：{', '.join(sorted(missing))}")
            session.execute(delete(UserKnowledgeBaseRecord).where(UserKnowledgeBaseRecord.user_id == user_id))
            now = _now()
            session.add_all(
                [
                    UserKnowledgeBaseRecord(
                        user_id=user_id,
                        knowledge_base_id=knowledge_base_id,
                        granted_by=actor_id,
                        created_at=now,
                    )
                    for knowledge_base_id in unique_ids
                ]
            )
            self._audit(session, actor_id, "user.knowledge_bases", "user", user_id, {"knowledge_base_ids": unique_ids})
        return unique_ids

    def user_can_access(self, user: User, knowledge_base_id: str) -> bool:
        with Session(self.engine) as session:
            knowledge_base = session.get(KnowledgeBaseRecord, knowledge_base_id)
            if knowledge_base is None or not knowledge_base.active:
                return False
            if user.role == "admin":
                return True
            return session.get(UserKnowledgeBaseRecord, (user.id, knowledge_base_id)) is not None

    def _profile(self, row: ModelProfileRecord) -> ModelProfile:
        try:
            options = json.loads(row.options_json)
        except json.JSONDecodeError as exc:
            raise ValueError("模型配置选项不是有效的 JSON") from exc
        return ModelProfile(
            id=row.id,
            name=row.name,
            purpose=row.purpose,
            protocol=row.protocol,
            base_url=row.base_url,
            api_key=self.cipher.decrypt(row.api_key_ciphertext),
            model_name=row.model_name,
            vector_dim=row.vector_dim,
            options=options,
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_profiles(self) -> list[ModelProfile]:
        with Session(self.engine) as session:
            rows = session.scalars(select(ModelProfileRecord).order_by(ModelProfileRecord.purpose, ModelProfileRecord.created_at)).all()
            return [self._profile(row) for row in rows]

    def get_profile(self, profile_id: str) -> ModelProfile | None:
        with Session(self.engine) as session:
            row = session.get(ModelProfileRecord, profile_id)
            return self._profile(row) if row else None

    def create_profile(self, *, name: str, purpose: str, protocol: str, base_url: str, api_key: str, model_name: str, vector_dim: int | None, options: dict[str, Any], actor_id: str) -> ModelProfile:
        allowed_protocols = {
            "chat": {"ollama", "openai_compatible"},
            "embedding": {"ollama", "openai_compatible"},
            "reranker": {"tei", "jina_compatible", "ollama_reranker"},
        }
        if purpose not in allowed_protocols or protocol not in allowed_protocols[purpose]:
            raise ValueError("无效的模型用途或协议")
        profile_id = str(uuid4())
        now = _now()
        row = ModelProfileRecord(
            id=profile_id,
            name=name.strip(),
            purpose=purpose,
            protocol=protocol,
            base_url=base_url.rstrip("/"),
            api_key_ciphertext=self.cipher.encrypt(api_key),
            model_name=model_name.strip(),
            vector_dim=vector_dim,
            options_json=json.dumps(options),
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as session, session.begin():
            session.add(row)
            self._audit(session, actor_id, "model_profile.create", "model_profile", profile_id, {"purpose": purpose, "protocol": protocol, "model": model_name})
        return self.get_profile(profile_id)  # type: ignore[return-value]

    def update_profile(self, profile_id: str, values: dict[str, Any], actor_id: str) -> ModelProfile:
        allowed = {"name", "protocol", "base_url", "model_name", "vector_dim", "options", "enabled"}
        updates = {key: value for key, value in values.items() if key in allowed and value is not None}
        with Session(self.engine) as session, session.begin():
            row = session.get(ModelProfileRecord, profile_id)
            if row is None:
                raise KeyError(profile_id)
            for key, value in updates.items():
                if key == "options":
                    row.options_json = json.dumps(value)
                elif key == "base_url":
                    row.base_url = value.rstrip("/")
                else:
                    setattr(row, key, value)
            if "api_key" in values:
                row.api_key_ciphertext = self.cipher.encrypt(values["api_key"] or "")
            row.updated_at = _now()
            self._audit(session, actor_id, "model_profile.update", "model_profile", profile_id, {"fields": sorted(updates)})
        return self.get_profile(profile_id)  # type: ignore[return-value]

    def delete_profile(self, profile_id: str, actor_id: str) -> None:
        active_ids = {
            self.get_setting("active_chat_profile_id"),
            self.get_setting("active_embedding_profile_id"),
            self.get_setting("active_reranker_profile_id"),
        }
        if profile_id in active_ids:
            raise ValueError("当前启用的模型配置不能删除")
        with Session(self.engine) as session, session.begin():
            row = session.get(ModelProfileRecord, profile_id)
            if row is None:
                raise KeyError(profile_id)
            session.delete(row)
            self._audit(session, actor_id, "model_profile.delete", "model_profile", profile_id, {})

    def get_setting(self, key: str, default: str = "") -> str:
        with Session(self.engine) as session:
            row = session.get(AppSettingRecord, key)
            return row.value if row else default

    def set_setting(self, key: str, value: str, actor_id: str | None = None) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.get(AppSettingRecord, key)
            if row is None:
                session.add(AppSettingRecord(key=key, value=value, updated_at=_now()))
            else:
                row.value = value
                row.updated_at = _now()
            self._audit(session, actor_id, "setting.update", "setting", key, {"value": value})

    def activate_chat(self, profile_id: str, actor_id: str) -> None:
        profile = self.get_profile(profile_id)
        if profile is None or profile.purpose != "chat" or not profile.enabled:
            raise ValueError("无效的对话模型配置")
        self.set_setting("active_chat_profile_id", profile_id, actor_id)

    def activate_reranker(self, profile_id: str, actor_id: str) -> None:
        profile = self.get_profile(profile_id)
        if profile is None or profile.purpose != "reranker" or not profile.enabled:
            raise ValueError("无效的重排序模型配置")
        self.set_setting("active_reranker_profile_id", profile_id, actor_id)

    def deactivate_reranker(self, actor_id: str) -> None:
        self.set_setting("active_reranker_profile_id", "", actor_id)

    def create_index_job(self, profile_id: str, knowledge_base_id: str, collection_name: str, actor_id: str) -> IndexJob:
        job_id = str(uuid4())
        now = _now()
        with Session(self.engine) as session, session.begin():
            session.add(
                IndexJobRecord(
                    id=job_id,
                    profile_id=profile_id,
                    knowledge_base_id=knowledge_base_id,
                    collection_name=collection_name,
                    status="queued",
                    total_chunks=0,
                    completed_chunks=0,
                    error="",
                    created_at=now,
                    updated_at=now,
                )
            )
            self._audit(session, actor_id, "embedding.reindex", "index_job", job_id, {"profile_id": profile_id, "knowledge_base_id": knowledge_base_id})
        return self.get_index_job(job_id)  # type: ignore[return-value]

    def update_index_job(self, job_id: str, *, status: str | None = None, total_chunks: int | None = None, completed_chunks: int | None = None, error: str | None = None) -> None:
        values = {"status": status, "total_chunks": total_chunks, "completed_chunks": completed_chunks, "error": error}
        with Session(self.engine) as session, session.begin():
            row = session.get(IndexJobRecord, job_id)
            if row is None:
                return
            for key, value in values.items():
                if value is not None:
                    setattr(row, key, value)
            row.updated_at = _now()

    def get_index_job(self, job_id: str) -> IndexJob | None:
        with Session(self.engine) as session:
            row = session.get(IndexJobRecord, job_id)
            if row is None:
                return None
            return IndexJob(
                id=row.id,
                profile_id=row.profile_id,
                knowledge_base_id=row.knowledge_base_id,
                collection_name=row.collection_name,
                status=row.status,
                total_chunks=row.total_chunks,
                completed_chunks=row.completed_chunks,
                error=row.error,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    def _audit(self, session: Session, actor_id: str | None, action: str, target_type: str, target_id: str, detail: dict[str, Any]) -> None:
        session.add(
            AuditLogRecord(
                actor_user_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail_json=json.dumps(detail, ensure_ascii=False),
                created_at=_now(),
            )
        )

    @staticmethod
    def _user(row: UserRecord) -> User:
        return User(row.id, row.username, row.display_name, row.role, row.active)

    @staticmethod
    def _knowledge_base(row: KnowledgeBaseRecord) -> KnowledgeBase:
        return KnowledgeBase(row.id, row.name, row.description, row.active)
