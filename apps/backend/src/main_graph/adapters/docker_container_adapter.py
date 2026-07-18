import asyncio
import logging
import os

from src.domain.ports.container_run_port import ContainerRunPort

logger = logging.getLogger(__name__)

_TIMEOUT = 300


class DockerContainerAdapter(ContainerRunPort):
    async def run(
        self,
        image: str,
        command: str,
        volume: str | None = None,
        run_as_root: bool = False,
    ) -> tuple[int, str, str]:
        cmd = ["docker", "run", "--rm"]
        if volume:
            cmd += ["-v", volume]
        if not run_as_root:
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        cmd += ["--entrypoint", "sh", image, "-c", command]
        logger.info("docker: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"timed out after {_TIMEOUT}s"

        assert proc.returncode is not None  # communicate() waits for exit
        return (
            proc.returncode,
            stdout_b.decode(errors="replace"),
            stderr_b.decode(errors="replace")[:3000],
        )
