from pydantic import BaseModel


class RegistryEntry(BaseModel):
    dep_name: str = ""
    last_publish: str | None = None
    weekly_downloads: int | None = None
    is_deprecated: bool = False
    maintainers_count: int | None = None
