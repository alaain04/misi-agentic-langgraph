from abc import ABC, abstractmethod


class ContainerRunPort(ABC):
    @abstractmethod
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
        secret_env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Run a container. Returns (returncode, stdout, stderr).

        `secret_env` values are delivered via Docker's bare `-e VARNAME`
        form (name only, no `=value`) so they flow through process
        environment inheritance only — the value never appears in the
        constructed command list, which adapters log verbatim.
        """
        ...
