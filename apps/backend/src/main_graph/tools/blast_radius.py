from __future__ import annotations

import json
import shlex

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort

_TEST_OR_SCRIPT_MARKERS = (
    "test",
    "tests",
    "__tests__",
    "spec",
    "e2e",
    "fixtures",
    "mocks",
    "scripts",
    "build",
    "dist",
)


def _is_test_or_script(path: str) -> bool:
    return any(part in _TEST_OR_SCRIPT_MARKERS for part in path.split("/"))


def make_blast_radius_tool(repo_path: str, container: ContainerRunPort, image: str):
    @tool
    async def blast_radius(package_name: str, depth: int = 3) -> dict:
        """Compute the real import/usage graph blast radius of a risky
        dependency: which files actually import it, how many, and whether
        that usage is isolated to tests/scripts or reaches production code."""
        command = (
            f"codegraph impact {shlex.quote(package_name)} "
            f"--json --depth {depth} -p /workspace"
        )
        rc, stdout, stderr = await container.run(
            image=image,
            command=command,
            volume=f"{repo_path}:/workspace",
            run_as_root=True,
        )
        if rc != 0:
            return {
                "package_name": package_name,
                "available": False,
                "error": stderr[:300] or f"exit {rc}",
            }

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return {
                "package_name": package_name,
                "available": False,
                "error": "unparseable codegraph output",
            }

        if not isinstance(data, dict) or "affected" not in data:
            # e.g. "Symbol not found" plain text swallowed the --json flag.
            return {
                "package_name": package_name,
                "available": True,
                "affected_file_count": 0,
                "affected_files": [],
            }

        files = sorted(
            {
                f"{a['filePath']}:{a.get('startLine', '?')}"
                for a in data.get("affected", [])
                if a.get("filePath")
            }
        )
        prod_files = [f for f in files if not _is_test_or_script(f)]

        return {
            "package_name": package_name,
            "available": True,
            "affected_file_count": len(files),
            "affected_files": files[:50],
            "production_file_count": len(prod_files),
            "isolated_to_tests_or_scripts": len(prod_files) == 0 and len(files) > 0,
            "node_count": data.get("nodeCount", 0),
            "depth_searched": data.get("depth", depth),
        }

    return blast_radius
