from dataclasses import dataclass
from datetime import datetime


@dataclass
class Job:
    job_id: str
    packages: list[str]
    total: int
    completed: int
    failed: int
    status: str  # "pending" | "running" | "done"
    created_at: datetime
    updated_at: datetime
