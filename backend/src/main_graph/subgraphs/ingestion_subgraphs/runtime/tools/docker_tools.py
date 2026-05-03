"""Tools for Docker container execution of npm scripts."""

from __future__ import annotations

import json
import subprocess
import time

from langchain_core.tools import tool


@tool
def run_docker_install(
    package_dir: str,
    memory_limit: str,
    cpu_limit: float,
    node_image: str,
) -> str:
    """
    Install npm dependencies inside Docker using npm install --ignore-scripts.

    Returns JSON: {success, stdout, stderr, exit_code}.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{package_dir}:/app",
        "-w",
        "/app",
        "--memory",
        memory_limit,
        "--cpus",
        str(cpu_limit),
        node_image,
        "npm",
        "install",
        "--ignore-scripts",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)  # noqa: S603
        return json.dumps(
            {
                "success": proc.returncode == 0,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
            }
        )
    except subprocess.TimeoutExpired:
        return json.dumps(
            {
                "success": False,
                "stdout": "",
                "stderr": "npm install timed out after 600s",
                "exit_code": -1,
            }
        )
    except FileNotFoundError:
        return json.dumps(
            {
                "success": False,
                "stdout": "",
                "stderr": "docker binary not found",
                "exit_code": -1,
            }
        )
    except Exception as exc:
        return json.dumps(
            {"success": False, "stdout": "", "stderr": str(exc), "exit_code": -1}
        )


@tool
def run_docker_script(
    script_name: str,
    package_dir: str,
    timeout_seconds: int,
    memory_limit: str,
    cpu_limit: float,
    node_image: str,
) -> str:
    """
    Run a single npm script inside a Docker container and capture output.

    Returns JSON: {exit_code, stdout, stderr, duration_seconds, timed_out}.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{package_dir}:/app",
        "-w",
        "/app",
        "--memory",
        memory_limit,
        "--cpus",
        str(cpu_limit),
        node_image,
        "npm",
        "run",
        script_name,
    ]
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds
        )  # noqa: S603
        duration = time.perf_counter() - start
        return json.dumps(
            {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "duration_seconds": round(duration, 3),
                "timed_out": False,
            }
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start
        return json.dumps(
            {
                "exit_code": -1,
                "stdout": (exc.output or b"").decode("utf-8", errors="replace"),
                "stderr": f"Script '{script_name}' timed out after {timeout_seconds}s",
                "duration_seconds": round(duration, 3),
                "timed_out": True,
            }
        )
    except FileNotFoundError:
        return json.dumps(
            {
                "exit_code": -1,
                "stdout": "",
                "stderr": "docker binary not found",
                "duration_seconds": 0.0,
                "timed_out": False,
            }
        )
    except Exception as exc:
        return json.dumps(
            {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(exc),
                "duration_seconds": 0.0,
                "timed_out": False,
            }
        )
