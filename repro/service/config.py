from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str | None
    object_backend: str
    object_endpoint: str | None
    object_region: str
    object_bucket: str
    object_access_key: str | None
    object_secret_key: str | None
    object_public_base: str | None
    local_object_root: Path
    ephemeral_blob_ttl_hours: int
    max_database_blob_bytes: int
    max_pdf_bytes: int
    github_owner: str
    github_token: str | None
    github_api: str

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL") or f"sqlite:///{REPO / 'runs' / 'service.db'}"
        # Render sometimes supplies the old postgres:// spelling.
        if database_url.startswith("postgres://"):
            database_url = "postgresql+psycopg://" + database_url.removeprefix("postgres://")
        elif database_url.startswith("postgresql://") and "+psycopg" not in database_url:
            database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
        return cls(
            database_url=database_url,
            redis_url=os.environ.get("REDIS_URL"),
            object_backend=os.environ.get("OBJECT_STORAGE_BACKEND", "auto").lower(),
            object_endpoint=os.environ.get("S3_ENDPOINT_URL"),
            object_region=os.environ.get("S3_REGION", "auto"),
            object_bucket=os.environ.get("S3_BUCKET", "snapshot-artifacts"),
            object_access_key=os.environ.get("S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID"),
            object_secret_key=os.environ.get("S3_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            object_public_base=os.environ.get("S3_PUBLIC_BASE_URL"),
            local_object_root=Path(os.environ.get("LOCAL_OBJECT_ROOT", REPO / "runs" / "objects")),
            ephemeral_blob_ttl_hours=int(os.environ.get("EPHEMERAL_BLOB_TTL_HOURS", "72")),
            max_database_blob_bytes=int(os.environ.get("MAX_DATABASE_BLOB_BYTES", 50 * 1024 * 1024)),
            max_pdf_bytes=int(os.environ.get("MAX_PDF_BYTES", 50 * 1024 * 1024)),
            github_owner=os.environ.get("GITHUB_OWNER", "persistentepiphany"),
            github_token=os.environ.get("GITHUB_USER_TOKEN"),
            github_api=os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
        )


settings = Settings.from_env()
