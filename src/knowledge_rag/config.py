from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_embed_model: str = "qwen3-embedding:8b"
    ollama_chat_model: str = "qwen3.5:9b"
    ollama_keep_alive: int = 0
    mineru_base_url: str = "http://127.0.0.1:7860"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "knowledge_chunks_v1"
    milvus_vector_dim: int = 4096
    data_dir: Path = Path("data")
    chunk_target_chars: int = 800
    chunk_max_chars: int = 1200
    chunk_overlap_chars: int = 120
    retrieval_top_k: int = 6
    retrieval_candidate_k: int = 20
    retrieval_min_score: float = 0.35
    app_db_path: Path = Path("data/app.db")
    database_url: str = ""
    auth_required: bool = True
    auth_session_hours: int = 24
    auth_cookie_secure: bool = False
    model_secret_key: str = ""
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        # CLI and Uvicorn do not load .env automatically. Read the file from the
        # launch directory while preserving explicitly exported environment values.
        load_dotenv(Path.cwd() / ".env", override=False)
        return cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url),
            ollama_embed_model=os.getenv("OLLAMA_EMBED_MODEL", cls.ollama_embed_model),
            ollama_chat_model=os.getenv("OLLAMA_CHAT_MODEL", cls.ollama_chat_model),
            ollama_keep_alive=int(os.getenv("OLLAMA_KEEP_ALIVE", str(cls.ollama_keep_alive))),
            mineru_base_url=os.getenv("MINERU_BASE_URL", cls.mineru_base_url),
            milvus_uri=os.getenv("MILVUS_URI", cls.milvus_uri),
            milvus_collection=os.getenv("MILVUS_COLLECTION", cls.milvus_collection),
            milvus_vector_dim=int(os.getenv("MILVUS_VECTOR_DIM", str(cls.milvus_vector_dim))),
            data_dir=Path(os.getenv("RAG_DATA_DIR", str(cls.data_dir))),
            chunk_target_chars=int(os.getenv("RAG_CHUNK_TARGET_CHARS", str(cls.chunk_target_chars))),
            chunk_max_chars=int(os.getenv("RAG_CHUNK_MAX_CHARS", str(cls.chunk_max_chars))),
            chunk_overlap_chars=int(os.getenv("RAG_CHUNK_OVERLAP_CHARS", str(cls.chunk_overlap_chars))),
            retrieval_top_k=int(os.getenv("RAG_RETRIEVAL_TOP_K", str(cls.retrieval_top_k))),
            retrieval_candidate_k=int(os.getenv("RAG_RETRIEVAL_CANDIDATE_K", str(cls.retrieval_candidate_k))),
            retrieval_min_score=float(os.getenv("RAG_RETRIEVAL_MIN_SCORE", str(cls.retrieval_min_score))),
            app_db_path=Path(os.getenv("RAG_APP_DB_PATH", str(cls.app_db_path))),
            database_url=os.getenv("DATABASE_URL", ""),
            auth_required=_env_bool("RAG_AUTH_REQUIRED", cls.auth_required),
            auth_session_hours=int(os.getenv("RAG_AUTH_SESSION_HOURS", str(cls.auth_session_hours))),
            auth_cookie_secure=_env_bool("RAG_AUTH_COOKIE_SECURE", cls.auth_cookie_secure),
            model_secret_key=os.getenv("RAG_MODEL_SECRET_KEY", ""),
            bootstrap_admin_username=os.getenv("RAG_BOOTSTRAP_ADMIN_USERNAME", ""),
            bootstrap_admin_password=os.getenv("RAG_BOOTSTRAP_ADMIN_PASSWORD", ""),
        )
