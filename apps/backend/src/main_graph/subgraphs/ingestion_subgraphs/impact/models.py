from pydantic import BaseModel, Field


class ImpactEntry(BaseModel):
    dep_name: str = ""
    usage_count: int = 0
    affected_files: list[str] = Field(default_factory=list)
    api_surface_used: list[str] = Field(default_factory=list)
    usage_summary: str = ""
    direct_dependents: int = 0
    transitive_dependents: int = 0
    max_depth: int = 0
    blast_radius_summary: str = ""
