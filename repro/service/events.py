from __future__ import annotations

import json
import time

from sqlalchemy.orm import Session

from .models import Event, Job


STAGE_DESCRIPTIONS = {
    "INGEST": "Fetch and validate the paper and its metadata.",
    "EXTRACT": "Extract searchable text and verify PDF provenance.",
    "PREFLIGHT": "Classify claims, check code availability, and resolve required data.",
    "G1": "Automatically validate and freeze the experiment plan before compute.",
    "P1": "Build and smoke-test the reproducible environment on Daytona.",
    "P2": "Run preregistered experiments and controls in Daytona sandboxes.",
    "P3": "Judge observations against the frozen acceptance rules.",
    "P4": "Run one separately approved adaptive follow-up when required.",
    "PACKAGE": "Assemble reports, code, manifests, hashes, and evidence for review.",
    "G3": "Wait for explicit approval before publishing any GitHub repository.",
    "GITHUB_PUBLISH": "Create or update the private GitHub evidence repository.",
}


def emit(session: Session, *, kind: str, stage: str, payload: dict | None = None,
         job_id: str | None = None, paper_id: str | None = None,
         source: str = "render") -> Event:
    event = Event(job_id=job_id, paper_id=paper_id, kind=kind, stage=stage,
                  source=source, payload_json=json.dumps(payload or {}, sort_keys=True),
                  created_at=time.time())
    session.add(event)
    if job_id:
        job = session.get(Job, job_id)
        if job:
            job.updated_at = time.time()
            job.stage = stage
    session.flush()
    return event


def event_dict(event: Event) -> dict:
    return {"id": event.id, "t": event.created_at, "kind": event.kind,
            "stage": event.stage, "source": event.source,
            "payload": json.loads(event.payload_json or "{}")}
