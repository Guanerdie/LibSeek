from __future__ import annotations

JOB_WORKER_HEARTBEAT_PREFIX = "job-worker:"
_MAX_WORKER_ID_LENGTH = 180


def job_worker_id(instance_id: str) -> str:
    normalized = instance_id.strip()
    if normalized.startswith(JOB_WORKER_HEARTBEAT_PREFIX):
        normalized = normalized.removeprefix(JOB_WORKER_HEARTBEAT_PREFIX).strip()
    if not normalized:
        raise ValueError("job worker instance id must not be empty")
    return f"{JOB_WORKER_HEARTBEAT_PREFIX}{normalized}"[:_MAX_WORKER_ID_LENGTH]
