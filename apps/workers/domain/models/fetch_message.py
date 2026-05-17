from dataclasses import dataclass


@dataclass
class FetchMessage:
    job_id: str
    entity_type: str
    name: str
