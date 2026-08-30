from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from .config import Settings, settings
from .models import EphemeralBlob


class ObjectStoreError(RuntimeError):
    pass


class ObjectStore:
    """S3, shared TTL-database, or local-development object storage.

    `database` is intentionally temporary: blobs expire automatically and are
    capped per object. Unlike Render's local filesystem it is shared by the web
    and worker services, which is required for server-fetched arXiv papers.
    """

    BACKENDS = {"auto", "s3", "database", "filesystem"}

    def __init__(self, config: Settings = settings):
        self.config = config
        requested = config.object_backend
        if requested not in self.BACKENDS:
            raise ObjectStoreError(f"unknown OBJECT_STORAGE_BACKEND: {requested}")
        s3_configured = bool(config.object_endpoint or
                             (config.object_access_key and config.object_secret_key))
        if requested == "auto":
            requested = ("s3" if s3_configured else
                         "database" if not config.database_url.startswith("sqlite") else
                         "filesystem")
        self.backend = requested
        self._client = None
        self._blob_sessions = None
        if self.backend == "s3":
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=config.object_endpoint,
                region_name=config.object_region,
                aws_access_key_id=config.object_access_key,
                aws_secret_access_key=config.object_secret_key,
            )
        elif self.backend == "database":
            connect_args = {"check_same_thread": False} if config.database_url.startswith("sqlite") else {}
            blob_engine = create_engine(config.database_url, pool_pre_ping=True, connect_args=connect_args)
            self._blob_sessions = sessionmaker(bind=blob_engine, expire_on_commit=False)

    @property
    def is_remote(self) -> bool:
        return self.backend == "s3"

    @property
    def is_shared(self) -> bool:
        return self.backend in {"s3", "database"}

    @property
    def is_ephemeral(self) -> bool:
        return self.backend == "database"

    @property
    def retention_hours(self) -> int | None:
        return self.config.ephemeral_blob_ttl_hours if self.is_ephemeral else None

    def _local_path(self, key: str) -> Path:
        root = self.config.local_object_root.resolve()
        path = (root / key).resolve()
        if root != path and root not in path.parents:
            raise ObjectStoreError("object key escapes local storage root")
        return path

    def _database_session(self):
        if self._blob_sessions is None:
            raise ObjectStoreError("database object backend is not initialized")
        return self._blob_sessions()

    def cleanup_expired(self) -> int:
        if self.backend != "database":
            return 0
        with self._database_session() as session, session.begin():
            result = session.execute(delete(EphemeralBlob).where(EphemeralBlob.expires_at <= time.time()))
            return int(result.rowcount or 0)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        digest = hashlib.sha256(data).hexdigest()
        if self.backend == "s3":
            self._client.put_object(Bucket=self.config.object_bucket, Key=key, Body=data,
                                    ContentType=content_type)
        elif self.backend == "database":
            if len(data) > self.config.max_database_blob_bytes:
                raise ObjectStoreError(
                    f"object exceeds temporary database limit of {self.config.max_database_blob_bytes} bytes"
                )
            now = time.time()
            expires_at = now + self.config.ephemeral_blob_ttl_hours * 3600
            with self._database_session() as session, session.begin():
                blob = session.get(EphemeralBlob, key)
                if blob is None:
                    blob = EphemeralBlob(object_key=key, data=data, content_type=content_type,
                                         sha256=digest, size=len(data), created_at=now,
                                         updated_at=now, expires_at=expires_at)
                    session.add(blob)
                else:
                    blob.data, blob.content_type = data, content_type
                    blob.sha256, blob.size = digest, len(data)
                    blob.updated_at, blob.expires_at = now, expires_at
            self.cleanup_expired()
        else:
            path = self._local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return digest

    def get_bytes(self, key: str, max_bytes: int | None = None) -> bytes:
        if self.backend == "s3":
            response = self._client.get_object(Bucket=self.config.object_bucket, Key=key)
            data = response["Body"].read((max_bytes + 1) if max_bytes else None)
        elif self.backend == "database":
            with self._database_session() as session, session.begin():
                blob = session.get(EphemeralBlob, key)
                if blob is None or blob.expires_at <= time.time():
                    if blob is not None:
                        session.delete(blob)
                    raise ObjectStoreError(f"temporary object is missing or expired: {key}")
                if max_bytes is not None and blob.size > max_bytes:
                    raise ObjectStoreError(f"object exceeds {max_bytes} bytes")
                data = bytes(blob.data)
        else:
            path = self._local_path(key)
            if max_bytes is not None and path.stat().st_size > max_bytes:
                raise ObjectStoreError(f"object exceeds {max_bytes} bytes")
            data = path.read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            raise ObjectStoreError(f"object exceeds {max_bytes} bytes")
        return data

    def head(self, key: str) -> dict:
        if self.backend == "s3":
            response = self._client.head_object(Bucket=self.config.object_bucket, Key=key)
            return {"size": int(response["ContentLength"]), "content_type": response.get("ContentType"),
                    "expires_at": None}
        if self.backend == "database":
            with self._database_session() as session, session.begin():
                row = session.execute(
                    select(EphemeralBlob.size, EphemeralBlob.content_type, EphemeralBlob.expires_at)
                    .where(EphemeralBlob.object_key == key)
                ).one_or_none()
                if row is None or row.expires_at <= time.time():
                    if row is not None:
                        session.execute(delete(EphemeralBlob).where(EphemeralBlob.object_key == key))
                    raise ObjectStoreError(f"temporary object is missing or expired: {key}")
                return {"size": row.size, "content_type": row.content_type,
                        "expires_at": row.expires_at}
        path = self._local_path(key)
        return {"size": path.stat().st_size, "content_type": None, "expires_at": None}

    def exists(self, key: str | None) -> bool:
        if not key:
            return False
        try:
            self.head(key)
            return True
        except (OSError, ObjectStoreError):
            return False

    def presign_put(self, key: str, content_type: str, expires_s: int = 900) -> str:
        if self.backend != "s3":
            return f"/api/papers/uploads/local/{quote(key, safe='')}"
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.config.object_bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_s,
        )

    def presign_get(self, key: str, expires_s: int = 900) -> str:
        if self.backend == "s3" and self.config.object_public_base:
            return f"{self.config.object_public_base.rstrip('/')}/{quote(key)}"
        if self.backend != "s3":
            return f"/api/artifacts/local/{quote(key, safe='')}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.config.object_bucket, "Key": key},
            ExpiresIn=expires_s,
        )

    def delete(self, key: str) -> None:
        if self.backend == "s3":
            self._client.delete_object(Bucket=self.config.object_bucket, Key=key)
        elif self.backend == "database":
            with self._database_session() as session, session.begin():
                blob = session.get(EphemeralBlob, key)
                if blob is not None:
                    session.delete(blob)
        else:
            path = self._local_path(key)
            if path.is_file():
                path.unlink()


store = ObjectStore()
