from __future__ import annotations

import json
import re
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

# Matches filename-suffix test conventions (e.g. foo.spec.ts, foo.test.tsx)
# that directory-segment matching alone can't catch.
_TEST_FILENAME_PATTERN = re.compile(r"\.(test|spec)\.[jt]sx?\b")


def _is_test_or_script(path: str) -> bool:
    file_path = path.split(":")[0]  # strip the trailing ":startLine"
    if any(part in _TEST_OR_SCRIPT_MARKERS for part in file_path.split("/")):
        return True
    return bool(_TEST_FILENAME_PATTERN.search(file_path))


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
            if "not found" in stdout.lower():
                # codegraph prints a plain-text "Symbol ... not found" line and
                # silently ignores --json when the package has zero usages —
                # that's a real answer (isolated dependency), not a tool error.
                data = {}
            else:
                return {
                    "package_name": package_name,
                    "available": False,
                    "error": "unparseable codegraph output",
                }

        if not isinstance(data, dict) or "affected" not in data:
            return {
                "package_name": package_name,
                "available": True,
                "affected_file_count": 0,
                "affected_files": [],
                "production_file_count": 0,
                "isolated_to_tests_or_scripts": False,
                "node_count": 0,
                "depth_searched": depth,
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
