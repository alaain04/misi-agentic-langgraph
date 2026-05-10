import logging

import nats
from nats.aio.client import Client
from nats.js import JetStreamContext
from nats.js.api import RetentionPolicy, StreamConfig

from src.config import settings

logger = logging.getLogger(__name__)

STREAM_NAME = "ENTITY_FETCH"
_SUBJECT_PREFIX = "entity.fetch"


def subject_for(entity_type: str) -> str:
    return f"{_SUBJECT_PREFIX}.{entity_type}"


_nc: Client | None = None
_js: JetStreamContext | None = None


async def connect() -> None:
    global _nc, _js
    _nc = await nats.connect(settings.nats_url)
    _js = _nc.jetstream()
    try:
        await _js.add_stream(
            StreamConfig(
                name=STREAM_NAME,
                subjects=[f"{_SUBJECT_PREFIX}.*"],
                retention=RetentionPolicy.WORK_QUEUE,
            )
        )
        logger.info("nats: stream %s created", STREAM_NAME)
    except Exception:
        logger.debug("nats: stream %s already exists", STREAM_NAME)


async def close() -> None:
    if _nc:
        await _nc.drain()


def get_js() -> JetStreamContext:
    if _js is None:
        raise RuntimeError("NATS not connected — call connect() first")
    return _js
