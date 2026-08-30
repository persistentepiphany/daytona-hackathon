from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor

from .config import settings


_local = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-local-worker")


def _resolve(path: str):
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


def enqueue(path: str, *args, job_id: str | None = None) -> str:
    """Enqueue durably in production; use one local thread only for development."""
    if settings.redis_url:
        from redis import Redis
        from rq import Queue, Retry

        rq_job = Queue("snapshot", connection=Redis.from_url(settings.redis_url)).enqueue(
            path, *args, job_id=job_id, job_timeout="2h", result_ttl=86400,
            retry=Retry(max=3, interval=[30, 120, 300]),
        )
        return rq_job.id
    _local.submit(_resolve(path), *args)
    return job_id or "local"
