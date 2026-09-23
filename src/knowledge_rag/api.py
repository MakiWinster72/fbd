from __future__ import annotations

import socket
from pathlib import Path
from uuid import uuid4
from dataclasses import asdict
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from knowledge_rag.admin_store import AdminStore, IndexJob, ModelProfile, User
from knowledge_rag.config import Settings
from knowledge_rag.document_import import DocumentImportManager, ImportJob
from knowledge_rag.providers import test_profile, validate_base_url
from knowledge_rag.rag import RagService, RetrievedSource
from knowledge_rag.service_registry import EmbeddingIndexManager, RagServiceRegistry

KnowledgeBaseId = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
SESSION_COOKIE = "knowledge_session"


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    knowledge_base_id: KnowledgeBaseId = "default"

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class ChatRequest(SearchRequest):
    pass


class SourceResponse(BaseModel):
    index: int
    score: float
    document_id: str
    document_name: str
    page_start: int
    page_end: int
    section_title: str
    excerpt: str
    vector_score: float = 0.0
    rerank_score: float | None = None

    @classmethod
    def from_source(cls, source: RetrievedSource) -> "SourceResponse":
        data = asdict(source)
        data.pop("content")
        return cls(**data)


class SearchResponse(BaseModel):
    sources: list[SourceResponse]


class ChatResponse(BaseModel):
    answer: str
    grounded: bool
    sources: list[SourceResponse]


class HealthResponse(BaseModel):
    status: str
    dependencies: dict[str, bool]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    active: bool

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(**asdict(user))


class AdminUserResponse(UserResponse):
    knowledge_base_ids: list[str]

    @classmethod
    def from_user_with_access(cls, user: User, knowledge_base_ids: list[str]) -> "AdminUserResponse":
        return cls(**asdict(user), knowledge_base_ids=knowledge_base_ids)


class UserCreateRequest(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{2,80}$")
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=10, max_length=256)
    role: Literal["admin", "user"] = "user"
    knowledge_base_ids: list[KnowledgeBaseId] = Field(default_factory=list, max_length=100)


class UserStatusRequest(BaseModel):
    active: bool


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=10, max_length=256)


class UserKnowledgeBasesRequest(BaseModel):
    knowledge_base_ids: list[KnowledgeBaseId] = Field(default_factory=list, max_length=100)


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str
    active: bool


class ModelProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    purpose: Literal["chat", "embedding", "reranker"]
    protocol: Literal["ollama", "openai_compatible", "tei", "jina_compatible", "ollama_reranker"]
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=2000)
    model_name: str = Field(min_length=1, max_length=300)
    vector_dim: int | None = Field(default=None, ge=1, le=100_000)
    options: dict[str, Any] = Field(default_factory=dict)


class ModelProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    protocol: Literal["ollama", "openai_compatible", "tei", "jina_compatible", "ollama_reranker"] | None = None
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=2000)
    clear_api_key: bool = False
    model_name: str | None = Field(default=None, min_length=1, max_length=300)
    vector_dim: int | None = Field(default=None, ge=1, le=100_000)
    options: dict[str, Any] | None = None
    enabled: bool | None = None


class ModelProfileResponse(BaseModel):
    id: str
    name: str
    purpose: str
    protocol: str
    base_url: str
    model_name: str
    vector_dim: int | None
    options: dict[str, Any]
    enabled: bool
    has_api_key: bool
    is_active: bool
    created_at: str
    updated_at: str


class ModelTestResponse(BaseModel):
    ok: bool
    sample: str | None = None
    dimensions: int | None = None


class IndexStartRequest(BaseModel):
    knowledge_base_id: KnowledgeBaseId = "default"


class IndexJobResponse(BaseModel):
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

    @classmethod
    def from_job(cls, job: IndexJob) -> "IndexJobResponse":
        return cls(**asdict(job))


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _url_open(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _port_open(parsed.hostname, port)


def _session_token(request: Request, cookie_token: str | None = None) -> str:
    if cookie_token:
        return cookie_token
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def get_current_user(request: Request, knowledge_session: str | None = Cookie(default=None)) -> User:
    if not request.app.state.settings.auth_required:
        return User("development", "development", "开发模式", "admin", True)
    token = _session_token(request, knowledge_session)
    user = request.app.state.admin_store.get_session_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def get_rag_service(request: Request) -> RagService:
    if request.app.state.fixed_rag_service is not None:
        return request.app.state.fixed_rag_service
    try:
        return request.app.state.rag_registry.get()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"RAG dependencies unavailable: {exc}") from exc


def _profile_response(profile: ModelProfile, store: AdminStore) -> ModelProfileResponse:
    active_key = {
        "chat": "active_chat_profile_id",
        "embedding": "active_embedding_profile_id",
        "reranker": "active_reranker_profile_id",
    }[profile.purpose]
    return ModelProfileResponse(
        id=profile.id,
        name=profile.name,
        purpose=profile.purpose,
        protocol=profile.protocol,
        base_url=profile.base_url,
        model_name=profile.model_name,
        vector_dim=profile.vector_dim,
        options=profile.options,
        enabled=profile.enabled,
        has_api_key=bool(profile.api_key),
        is_active=store.get_setting(active_key) == profile.id,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def create_app(
    rag_service: RagService | None = None,
    settings: Settings | None = None,
    admin_store: AdminStore | None = None,
    auth_required: bool | None = None,
) -> FastAPI:
    effective_settings = settings or Settings.from_env()
    if auth_required is not None:
        from dataclasses import replace
        effective_settings = replace(effective_settings, auth_required=auth_required)
    database = effective_settings.database_url or effective_settings.app_db_path
    store = admin_store or AdminStore(database, effective_settings.model_secret_key)
    store.seed_defaults(effective_settings)
    store.ensure_bootstrap_admin(effective_settings.bootstrap_admin_username, effective_settings.bootstrap_admin_password)
    registry = RagServiceRegistry(effective_settings, store)
    index_manager = EmbeddingIndexManager(effective_settings, store, registry)
    import_manager = DocumentImportManager(effective_settings, store)

    app = FastAPI(
        title="Knowledge RAG API",
        version="0.2.0",
        description="提供多用户知识库检索、模型配置和带页码引用的问答接口。",
    )
    app.state.settings = effective_settings
    app.state.admin_store = store
    app.state.fixed_rag_service = rag_service
    app.state.rag_registry = registry
    app.state.index_manager = index_manager

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        chat = store.get_profile(store.get_setting("active_chat_profile_id"))
        embedding = store.get_profile(store.get_setting("active_embedding_profile_id"))
        reranker_id = store.get_setting("active_reranker_profile_id")
        reranker = store.get_profile(reranker_id) if reranker_id else None
        dependencies = {
            "chat_model": _url_open(chat.base_url) if chat else False,
            "embedding_model": _url_open(embedding.base_url) if embedding else False,
            "milvus": _url_open(effective_settings.milvus_uri),
        }
        if reranker is not None:
            dependencies["reranker"] = _url_open(reranker.base_url)
        return HealthResponse(status="ok" if all(dependencies.values()) else "degraded", dependencies=dependencies)

    @app.post("/api/v1/auth/login", response_model=UserResponse)
    async def login(payload: LoginRequest, response: Response) -> UserResponse:
        user = await run_in_threadpool(store.authenticate, payload.username, payload.password)
        if user is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = await run_in_threadpool(store.create_session, user.id, effective_settings.auth_session_hours)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=effective_settings.auth_session_hours * 3600,
            httponly=True,
            secure=effective_settings.auth_cookie_secure,
            samesite="lax",
            path="/",
        )
        return UserResponse.from_user(user)

    @app.get("/api/v1/auth/me", response_model=UserResponse)
    async def me(user: User = Depends(get_current_user)) -> UserResponse:
        return UserResponse.from_user(user)

    @app.post("/api/v1/auth/logout", status_code=204)
    async def logout(request: Request, response: Response, knowledge_session: str | None = Cookie(default=None)) -> Response:
        token = _session_token(request, knowledge_session)
        if token:
            await run_in_threadpool(store.delete_session, token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.status_code = 204
        return response

    @app.get("/api/v1/knowledge-bases", response_model=list[KnowledgeBaseResponse])
    async def list_accessible_knowledge_bases(user: User = Depends(get_current_user)) -> list[KnowledgeBaseResponse]:
        knowledge_bases = await run_in_threadpool(store.list_knowledge_bases, user)
        return [KnowledgeBaseResponse(**asdict(item)) for item in knowledge_bases]

    @app.post("/api/v1/search", response_model=SearchResponse)
    async def search(payload: SearchRequest, request: Request, user: User = Depends(get_current_user)) -> SearchResponse:
        if not await run_in_threadpool(store.user_can_access, user, payload.knowledge_base_id):
            raise HTTPException(status_code=403, detail="没有访问该知识库的权限")
        service = get_rag_service(request)
        try:
            sources = await run_in_threadpool(service.retrieve, payload.question, payload.knowledge_base_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Search failed: {exc}") from exc
        return SearchResponse(sources=[SourceResponse.from_source(source) for source in sources])

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request, user: User = Depends(get_current_user)) -> ChatResponse:
        if not await run_in_threadpool(store.user_can_access, user, payload.knowledge_base_id):
            raise HTTPException(status_code=403, detail="没有访问该知识库的权限")
        service = get_rag_service(request)
        try:
            result = await run_in_threadpool(service.answer, payload.question, payload.knowledge_base_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Chat failed: {exc}") from exc
        return ChatResponse(
            answer=result.answer,
            grounded=result.grounded,
            sources=[SourceResponse.from_source(source) for source in result.sources],
        )

    @app.get("/api/v1/admin/users", response_model=list[AdminUserResponse])
    async def list_users(_: User = Depends(require_admin)) -> list[AdminUserResponse]:
        users = await run_in_threadpool(store.list_users)
        return [
            AdminUserResponse.from_user_with_access(user, await run_in_threadpool(store.user_knowledge_base_ids, user.id))
            for user in users
        ]

    @app.post("/api/v1/admin/users", response_model=AdminUserResponse, status_code=201)
    async def create_user(payload: UserCreateRequest, admin: User = Depends(require_admin)) -> AdminUserResponse:
        known_ids = {item.id for item in await run_in_threadpool(store.list_knowledge_bases)}
        missing = set(payload.knowledge_base_ids) - known_ids
        if missing:
            raise HTTPException(status_code=422, detail=f"知识库不存在：{', '.join(sorted(missing))}")
        try:
            user = await run_in_threadpool(store.create_user, payload.username, payload.display_name, payload.password, payload.role, admin.id)
            access = await run_in_threadpool(store.set_user_knowledge_bases, user.id, payload.knowledge_base_ids, admin.id)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return AdminUserResponse.from_user_with_access(user, access)

    @app.patch("/api/v1/admin/users/{user_id}/status", response_model=AdminUserResponse)
    async def update_user_status(user_id: str, payload: UserStatusRequest, admin: User = Depends(require_admin)) -> AdminUserResponse:
        try:
            user = await run_in_threadpool(store.set_user_active, user_id, payload.active, admin.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        access = await run_in_threadpool(store.user_knowledge_base_ids, user.id)
        return AdminUserResponse.from_user_with_access(user, access)

    @app.post("/api/v1/admin/users/{user_id}/reset-password", status_code=204)
    async def reset_user_password(user_id: str, payload: PasswordResetRequest, admin: User = Depends(require_admin)) -> Response:
        try:
            await run_in_threadpool(store.reset_user_password, user_id, payload.password, admin.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.put("/api/v1/admin/users/{user_id}/knowledge-bases", response_model=AdminUserResponse)
    async def update_user_knowledge_bases(user_id: str, payload: UserKnowledgeBasesRequest, admin: User = Depends(require_admin)) -> AdminUserResponse:
        try:
            access = await run_in_threadpool(store.set_user_knowledge_bases, user_id, payload.knowledge_base_ids, admin.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        user = store.get_user(user_id)
        return AdminUserResponse.from_user_with_access(user, access)  # type: ignore[arg-type]

    @app.get("/api/v1/admin/document-imports", response_model=list[ImportJob])
    async def list_document_imports(_: User = Depends(require_admin)) -> list[ImportJob]:
        return await run_in_threadpool(import_manager.list)

    @app.get("/api/v1/admin/document-imports/{job_id}", response_model=ImportJob)
    async def get_document_import(job_id: str, _: User = Depends(require_admin)) -> ImportJob:
        job = await run_in_threadpool(import_manager.get, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        return job

    @app.post("/api/v1/admin/document-imports", response_model=ImportJob, status_code=202)
    async def upload_document(
        file: UploadFile = File(...),
        knowledge_base_id: KnowledgeBaseId = Form(...),
        ocr_mode: Literal["auto", "force", "native"] = Form("auto"),
        duplicate_mode: Literal["skip", "replace"] = Form("skip"),
        _: User = Depends(require_admin),
    ) -> ImportJob:
        bases = await run_in_threadpool(store.list_knowledge_bases)
        if not any(base.id == knowledge_base_id and base.active for base in bases):
            raise HTTPException(status_code=400, detail="目标知识库不可用")
        name = Path(file.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in {".pdf", ".docx"}:
            raise HTTPException(status_code=400, detail="仅支持 PDF 和 DOCX")
        if not name or len(name) > 255:
            raise HTTPException(status_code=400, detail="无效文件名")
        directory = effective_settings.data_dir / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        upload_dir = directory / uuid4().hex
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / name
        size = 0
        try:
            with path.open("wb") as target:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="单文件超过 200 MB")
                    target.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="文件不能为空")
            return await run_in_threadpool(import_manager.start, path, name, knowledge_base_id, ocr_mode, duplicate_mode)
        except Exception:
            path.unlink(missing_ok=True)
            upload_dir.rmdir()
            raise
        finally:
            await file.close()

    @app.get("/api/v1/admin/mineru-status")
    async def mineru_status(_: User = Depends(require_admin)) -> dict[str, bool]:
        return {"reachable": await run_in_threadpool(_url_open, effective_settings.mineru_base_url)}

    @app.get("/api/v1/admin/knowledge-bases", response_model=list[KnowledgeBaseResponse])
    async def list_admin_knowledge_bases(_: User = Depends(require_admin)) -> list[KnowledgeBaseResponse]:
        return [KnowledgeBaseResponse(**asdict(item)) for item in await run_in_threadpool(store.list_knowledge_bases)]

    @app.get("/api/v1/admin/model-profiles", response_model=list[ModelProfileResponse])
    async def list_model_profiles(_: User = Depends(require_admin)) -> list[ModelProfileResponse]:
        profiles = await run_in_threadpool(store.list_profiles)
        return [_profile_response(profile, store) for profile in profiles]

    @app.post("/api/v1/admin/model-profiles", response_model=ModelProfileResponse, status_code=201)
    async def create_model_profile(payload: ModelProfileCreateRequest, admin: User = Depends(require_admin)) -> ModelProfileResponse:
        try:
            validate_base_url(payload.base_url)
            profile = await run_in_threadpool(store.create_profile, **payload.model_dump(), actor_id=admin.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _profile_response(profile, store)

    @app.patch("/api/v1/admin/model-profiles/{profile_id}", response_model=ModelProfileResponse)
    async def update_model_profile(profile_id: str, payload: ModelProfileUpdateRequest, admin: User = Depends(require_admin)) -> ModelProfileResponse:
        values = payload.model_dump(exclude_unset=True)
        clear_api_key = bool(values.pop("clear_api_key", False))
        if clear_api_key:
            values["api_key"] = ""
        if values.get("base_url"):
            try:
                validate_base_url(values["base_url"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            profile = await run_in_threadpool(store.update_profile, profile_id, values, admin.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型配置不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        registry.invalidate()
        return _profile_response(profile, store)

    @app.delete("/api/v1/admin/model-profiles/{profile_id}", status_code=204)
    async def delete_model_profile(profile_id: str, admin: User = Depends(require_admin)) -> Response:
        try:
            await run_in_threadpool(store.delete_profile, profile_id, admin.id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/v1/admin/model-profiles/{profile_id}/test", response_model=ModelTestResponse)
    async def test_model_profile(profile_id: str, admin: User = Depends(require_admin)) -> ModelTestResponse:
        profile = await run_in_threadpool(store.get_profile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        try:
            result = await run_in_threadpool(test_profile, profile)
            dimensions = result.get("dimensions")
            if profile.purpose == "embedding" and dimensions and dimensions != profile.vector_dim:
                profile = await run_in_threadpool(store.update_profile, profile.id, {"vector_dim": dimensions}, admin.id)
                registry.invalidate()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"模型连接测试失败：{exc}") from exc
        return ModelTestResponse(**result)

    @app.post("/api/v1/admin/model-profiles/{profile_id}/activate-chat", response_model=ModelProfileResponse)
    async def activate_chat(profile_id: str, admin: User = Depends(require_admin)) -> ModelProfileResponse:
        try:
            await run_in_threadpool(store.activate_chat, profile_id, admin.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        registry.invalidate()
        profile = store.get_profile(profile_id)
        return _profile_response(profile, store)  # type: ignore[arg-type]

    @app.post("/api/v1/admin/model-profiles/{profile_id}/activate-reranker", response_model=ModelProfileResponse)
    async def activate_reranker(profile_id: str, admin: User = Depends(require_admin)) -> ModelProfileResponse:
        try:
            await run_in_threadpool(store.activate_reranker, profile_id, admin.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        registry.invalidate()
        profile = store.get_profile(profile_id)
        return _profile_response(profile, store)  # type: ignore[arg-type]

    @app.post("/api/v1/admin/model-profiles/deactivate-reranker", status_code=204)
    async def deactivate_reranker(admin: User = Depends(require_admin)) -> Response:
        await run_in_threadpool(store.deactivate_reranker, admin.id)
        registry.invalidate()
        return Response(status_code=204)

    @app.post("/api/v1/admin/model-profiles/{profile_id}/activate-embedding", response_model=IndexJobResponse, status_code=202)
    async def activate_embedding(profile_id: str, payload: IndexStartRequest, admin: User = Depends(require_admin)) -> IndexJobResponse:
        try:
            job_id = index_manager.start(profile_id, payload.knowledge_base_id, admin.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = store.get_index_job(job_id)
        return IndexJobResponse.from_job(job)  # type: ignore[arg-type]

    @app.get("/api/v1/admin/index-jobs/{job_id}", response_model=IndexJobResponse)
    async def get_index_job(job_id: str, _: User = Depends(require_admin)) -> IndexJobResponse:
        job = await run_in_threadpool(store.get_index_job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="索引任务不存在")
        return IndexJobResponse.from_job(job)

    return app


app = create_app()
