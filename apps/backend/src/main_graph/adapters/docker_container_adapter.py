import asyncio
import os

from src.domain.ports.container_run_port import ContainerRunPort

_TIMEOUT = 300


class DockerContainerAdapter(ContainerRunPort):
    async def run(
        self, image: str, command: str, volume: str | None = None
    ) -> tuple[int, str, str]:
        cmd = ["docker", "run", "--rm"]
        if volume:
            cmd += ["-v", volume]
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        cmd += [image, "sh", "-c", command]

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

        return (
            proc.returncode,
            stdout_b.decode(errors="replace"),
            stderr_b.decode(errors="replace")[:3000],
        )
