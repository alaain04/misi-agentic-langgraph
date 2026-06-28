from abc import ABC, abstractmethod


class ContainerRunPort(ABC):
    @abstractmethod
    async def run(
        self, image: str, command: str, volume: str | None = None, run_as_root: bool = False
    ) -> tuple[int, str, str]:
        """Run a container. Returns (returncode, stdout, stderr)."""
        ...
