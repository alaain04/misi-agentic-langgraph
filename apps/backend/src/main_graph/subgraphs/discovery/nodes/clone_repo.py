"""Node: clone_repo — shallow-clone the repository into a temp directory."""

import logging
import os

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_GIT_IMAGE = "alpine/git"


def _clone_command(
    repo_url: str, github_token: str | None
) -> tuple[str, dict[str, str] | None]:
    """Build the clone command. When a token is present, auth is injected via
    a process-scoped `git -c http.extraHeader` override (never written to
    the cloned repo's own .git/config) referencing $GIT_TOKEN — the actual
    value is delivered to the shell only via the container's environment
    (see ContainerRunPort.run's secret_env), never as a literal here.

    `base64 -w0` disables line wrapping: a realistic PAT base64-encodes to
    well over the default 76-column wrap, and an embedded newline in the
    header value makes git/curl fail with "A libcurl function was given a
    bad argument".
    """
    if github_token:
        command = (
            'git -c http.extraHeader="AUTHORIZATION: basic '
            '$(printf \'x-access-token:%s\' "$GIT_TOKEN" | base64 -w0)" '
            f"clone --depth=1 --single-branch {repo_url} /workspace"
        )
        return command, {"GIT_TOKEN": github_token}
    return f"git clone --depth=1 --single-branch {repo_url} /workspace", None


async def clone_repo(state: DiscoveryState, config: RunnableConfig) -> dict:
    """Shallow-clone the repository. Sets repo_path; sets discovery_error on failure."""
    svc = get_services(config)
    container: ContainerRunPort = svc["container"]
    github_token = svc.get("github_token")

    job_id = state["job_id"]
    repo_url = state["repo_url"]
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    command, secret_env = _clone_command(repo_url, github_token)
    rc, _out, stderr = await container.run(
        image=_GIT_IMAGE,
        command=command,
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
        secret_env=secret_env,
    )

    if rc != 0:
        logger.error("clone_repo: failed rc=%d stderr=%s", rc, stderr[:300])
        return {
            "repo_path": tmp_dir,
            "discovery_error": stderr.strip() or "git clone failed",
        }

    logger.info("clone_repo: success repo_url=%s", repo_url)

    sha_rc, sha_out, _sha_err = await container.run(
        image=_GIT_IMAGE,
        command="cd /workspace && git rev-parse HEAD",
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
    )
    commit_sha = sha_out.strip() if sha_rc == 0 else ""
    return {"repo_path": tmp_dir, "commit_sha": commit_sha}
