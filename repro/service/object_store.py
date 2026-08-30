from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import quote

from .config import Settings, settings


class ObjectStoreError(RuntimeError):
    pass


class ObjectStore:
    """S3-compatible object storage with an explicit local-development backend."""

    def __init__(self, config: Settings = settings):
        self.config = config
        self._client = None
        if config.object_endpoint or (config.object_access_key and config.object_secret_key):
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=config.object_endpoint,
                region_name=config.object_region,
                aws_access_key_id=config.object_access_key,
                aws_secret_access_key=config.object_secret_key,
            )

    @property
    def is_remote(self) -> bool:
        return self._client is not None

    def _local_path(self, key: str) -> Path:
        root = self.config.local_object_root.resolve()
        path = (root / key).resolve()
        if root != path and root not in path.parents:
            raise ObjectStoreError("object key escapes local storage root")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if self._client:
            self._client.put_object(Bucket=self.config.object_bucket, Key=key, Body=data,
                                    ContentType=content_type)
        else:
            path = self._local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def get_bytes(self, key: str, max_bytes: int | None = None) -> bytes:
        if self._client:
            response = self._client.get_object(Bucket=self.config.object_bucket, Key=key)
            data = response["Body"].read((max_bytes + 1) if max_bytes else None)
        else:
            path = self._local_path(key)
            if max_bytes is not None and path.stat().st_size > max_bytes:
                raise ObjectStoreError(f"object exceeds {max_bytes} bytes")
            data = path.read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            raise ObjectStoreError(f"object exceeds {max_bytes} bytes")
        return data

    def head(self, key: str) -> dict:
        if self._client:
            response = self._client.head_object(Bucket=self.config.object_bucket, Key=key)
            return {"size": int(response["ContentLength"]), "content_type": response.get("ContentType")}
        path = self._local_path(key)
        return {"size": path.stat().st_size, "content_type": None}

    def presign_put(self, key: str, content_type: str, expires_s: int = 900) -> str:
        if not self._client:
            return f"/api/papers/uploads/local/{quote(key, safe='')}"
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.config.object_bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_s,
        )

    def presign_get(self, key: str, expires_s: int = 900) -> str:
        if self.config.object_public_base:
            return f"{self.config.object_public_base.rstrip('/')}/{quote(key)}"
        if not self._client:
            return f"/api/artifacts/local/{quote(key, safe='')}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.config.object_bucket, "Key": key},
            ExpiresIn=expires_s,
        )

    def delete(self, key: str) -> None:
        if self._client:
            self._client.delete_object(Bucket=self.config.object_bucket, Key=key)
        else:
            path = self._local_path(key)
            if path.is_file():
                path.unlink()


store = ObjectStore()
