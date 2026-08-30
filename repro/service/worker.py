from __future__ import annotations

from redis import Redis
from rq import Queue, Worker
from sqlalchemy import select

from .config import settings
from .database import init_db, session_scope
from .events import emit
from .models import Gate, Job, Paper, Upload
from .object_store import store
from .queueing import enqueue


def recover_interrupted_work() -> None:
    """A Render worker is a singleton; its startup is the recovery boundary.

    Re-running creates a new pipeline attempt while retaining the same public job
    and all prior events. Completed/package/G3 states are never re-executed.
    """
    with session_scope() as session:
        for paper in session.scalars(select(Paper).where(Paper.status == "ingesting")).all():
            enqueue("repro.service.tasks.ingest_arxiv", paper.id,
                    job_id=f"recover-ingest-{paper.id}")
        for upload in session.scalars(select(Upload).where(Upload.status == "verifying")).all():
            enqueue("repro.service.tasks.complete_upload", upload.id,
                    job_id=f"recover-upload-{upload.id}")
        for job in session.scalars(select(Job).where(Job.status.in_(["queued", "running", "publishing"]))).all():
            if job.status == "publishing":
                gate = session.scalar(select(Gate).where(Gate.job_id == job.id, Gate.gate == "G3"))
                if gate:
                    enqueue("repro.service.tasks.publish_github", job.id,
                            job_id=f"recover-github-{job.id}")
                continue
            if job.status == "running":
                job.status = "queued"
                emit(session, job_id=job.id, paper_id=job.paper_id, kind="run.recovered",
                     stage=job.stage, payload={"reason": "background worker restarted",
                                              "next_attempt": job.attempt + 1})
            enqueue("repro.service.tasks.run_pipeline", job.id,
                    job_id=f"recover-pipeline-{job.id}-{job.attempt + 1}")


def main() -> None:
    if not settings.redis_url:
        raise SystemExit("REDIS_URL is required for the production background worker")
    init_db()
    store.cleanup_expired()
    recover_interrupted_work()
    connection = Redis.from_url(settings.redis_url)
    worker = Worker([Queue("snapshot", connection=connection)], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
