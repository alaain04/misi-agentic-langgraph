"""NATS JetStream adapter — full pull-consumer implementation."""

import json
import logging
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, RetentionPolicy, StreamConfig
from nats.js.errors import BadRequestError, FetchTimeoutError

from domain.ports.messaging_port import MessageHandler, MessagingPort

logger = logging.getLogger(__name__)


class NATSJetStreamAdapter(MessagingPort):
    def __init__(self, nats_url: str, stream_name: str, subject_prefix: str) -> None:
        self._nats_url = nats_url
        self._stream_name = stream_name
        self._subject_prefix = subject_prefix
        self._client: NATSClient | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> None:
        self._client = await nats.connect(self._nats_url)
        self._js = self._client.jetstream()
        try:
            await self._js.add_stream(
                StreamConfig(
                    name=self._stream_name,
                    subjects=[f"{self._subject_prefix}.*"],
                    retention=RetentionPolicy.WORK_QUEUE,
                )
            )
            logger.info("nats: stream %s created", self._stream_name)
        except BadRequestError:
            logger.debug("nats: stream %s already exists", self._stream_name)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.drain()
            self._client = None
            self._js = None
        logger.info("nats: disconnected")

    async def publish(
        self,
        topic: str,
        message: dict[str, Any],
        attributes: dict[str, str] | None = None,
    ) -> str:
        if self._js is None:
            raise RuntimeError("call connect() before publishing")
        payload = json.dumps(message).encode()
        ack = await self._js.publish(topic, payload)
        return str(ack.seq)

    async def subscribe(self, queue: str, handler: MessageHandler) -> None:
        raise NotImplementedError("use pull_subscribe for JetStream consumers")

    async def acknowledge(self, receipt_handle: str) -> None:
        raise NotImplementedError("use ack(msg) directly")

    async def add_stream(self, name: str, subjects: list[str]) -> None:
        if self._js is None:
            raise RuntimeError("call connect() first")
        try:
            await self._js.add_stream(StreamConfig(name=name, subjects=subjects))
        except BadRequestError:
            pass

    async def pull_subscribe(
        self, stream: str, subject: str, durable: str, max_deliver: int
    ) -> Any:
        if self._js is None:
            raise RuntimeError("call connect() first")
        return await self._js.pull_subscribe(
            subject,
            durable=durable,
            stream=stream,
            config=ConsumerConfig(max_deliver=max_deliver),
        )

    async def pull_fetch(
        self, subscription: Any, batch: int, timeout: float
    ) -> list[Any]:
        try:
            return await subscription.fetch(batch, timeout=timeout)
        except FetchTimeoutError:
            return []

    async def ack(self, msg: Any) -> None:
        await msg.ack()

    async def nak(self, msg: Any, delay: float) -> None:
        await msg.nak(delay=delay)

    async def term(self, msg: Any) -> None:
        await msg.term()
