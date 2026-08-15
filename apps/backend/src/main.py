import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.db.connection import get_client
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.utils.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if settings.langsmith_api_key:
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_TRACING"] = "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_client().admin.command("ping")
    logger.info("startup check: MongoDB reachable")

    rc, _, stderr = await DockerContainerAdapter().run(
        image=settings.codegraph_docker_image, command="codegraph --version"
    )
    if rc != 0:
        raise RuntimeError(
            f"codegraph image '{settings.codegraph_docker_image}' is not "
            f"runnable (exit {rc}): {stderr}"
        )
    logger.info("startup check: codegraph image runnable")

    rc, _, stderr = await DockerContainerAdapter().run(
        image=settings.trivy_image, command="trivy --version"
    )
    if rc != 0:
        raise RuntimeError(
            f"trivy image '{settings.trivy_image}' is not runnable "
            f"(exit {rc}): {stderr}"
        )
    logger.info("startup check: trivy image runnable")

    rc, _, stderr = await DockerContainerAdapter().run(
        image=settings.gh_docker_image, command="gh --version"
    )
    if rc != 0:
        raise RuntimeError(
            f"gh image '{settings.gh_docker_image}' is not runnable "
            f"(exit {rc}): {stderr}"
        )
    logger.info("startup check: gh image runnable")

    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
