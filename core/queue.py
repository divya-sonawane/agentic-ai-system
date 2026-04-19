"""
Message Queue Abstraction
Supports Redis (production) with in-memory fallback (dev/test).
Provides enqueue, dequeue, dead-letter queue (DLQ) for failed messages.
"""

import asyncio
import json
import time
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Message:
    msg_id: str
    payload: Dict[str, Any]
    enqueued_at: float
    attempts: int = 0
    max_attempts: int = 3


class BaseQueue(ABC):
    @abstractmethod
    async def enqueue(self, payload: Dict[str, Any]) -> str:
        """Add a message to the queue. Returns message ID."""

    @abstractmethod
    async def dequeue(self, timeout: float = 5.0) -> Optional[Message]:
        """Fetch next message from the queue."""

    @abstractmethod
    async def ack(self, msg_id: str) -> None:
        """Acknowledge message processed successfully."""

    @abstractmethod
    async def nack(self, msg_id: str, error: str) -> None:
        """Negative ack - put back in queue or move to DLQ."""

    @abstractmethod
    async def queue_size(self) -> int:
        """Return current queue depth."""


class InMemoryQueue(BaseQueue):
    """
    Thread-safe async in-memory queue.
    Suitable for single-node dev/test. Drop-in for Redis in production.
    """

    def __init__(self, name: str = "default", dlq_threshold: int = 3):
        self.name = name
        self.dlq_threshold = dlq_threshold
        self._queue: asyncio.Queue = asyncio.Queue()
        self._inflight: Dict[str, Message] = {}
        self._dlq: list = []
        self._counter = 0

    async def enqueue(self, payload: Dict[str, Any]) -> str:
        self._counter += 1
        msg_id = f"msg-{self._counter:06d}-{int(time.time()*1000)}"
        msg = Message(
            msg_id=msg_id,
            payload=payload,
            enqueued_at=time.time(),
        )
        await self._queue.put(msg)
        logger.debug(f"[Queue:{self.name}] Enqueued {msg_id}")
        return msg_id

    async def dequeue(self, timeout: float = 5.0) -> Optional[Message]:
        try:
            msg = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            msg.attempts += 1
            self._inflight[msg.msg_id] = msg
            logger.debug(f"[Queue:{self.name}] Dequeued {msg.msg_id} (attempt {msg.attempts})")
            return msg
        except asyncio.TimeoutError:
            return None

    async def ack(self, msg_id: str) -> None:
        self._inflight.pop(msg_id, None)
        logger.debug(f"[Queue:{self.name}] ACK {msg_id}")

    async def nack(self, msg_id: str, error: str) -> None:
        msg = self._inflight.pop(msg_id, None)
        if msg is None:
            return
        if msg.attempts >= self.dlq_threshold:
            msg.payload["_error"] = error
            self._dlq.append(msg)
            logger.warning(f"[Queue:{self.name}] DLQ {msg_id} after {msg.attempts} attempts: {error}")
        else:
            # Re-enqueue with backoff hint
            await asyncio.sleep(2 ** msg.attempts)
            await self._queue.put(msg)
            logger.info(f"[Queue:{self.name}] Re-queued {msg_id} (attempt {msg.attempts})")

    async def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def dlq_size(self) -> int:
        return len(self._dlq)

    def dlq_messages(self) -> list:
        return list(self._dlq)


class RedisQueue(BaseQueue):
    """
    Production Redis-backed queue using redis-py async client.
    Falls back to InMemoryQueue if Redis unavailable.
    """

    def __init__(self, host: str = "localhost", port: int = 6379,
                 queue_name: str = "agent_tasks", dlq_name: str = "agent_dlq"):
        self.host = host
        self.port = port
        self.queue_name = queue_name
        self.dlq_name = dlq_name
        self._redis = None
        self._fallback: Optional[InMemoryQueue] = None

    async def _get_client(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
            client = aioredis.Redis(host=self.host, port=self.port, decode_responses=True)
            await client.ping()
            self._redis = client
            logger.info(f"[RedisQueue] Connected to Redis at {self.host}:{self.port}")
            return self._redis
        except Exception as e:
            if self._fallback is None:
                logger.warning(f"[RedisQueue] Redis unavailable ({e}), using in-memory fallback")
                self._fallback = InMemoryQueue(name=self.queue_name)
            return None

    async def enqueue(self, payload: Dict[str, Any]) -> str:
        client = await self._get_client()
        if client is None:
            return await self._fallback.enqueue(payload)

        msg_id = f"rmsg-{int(time.time()*1000)}"
        payload["_msg_id"] = msg_id
        await client.lpush(self.queue_name, json.dumps(payload))
        return msg_id

    async def dequeue(self, timeout: float = 5.0) -> Optional[Message]:
        client = await self._get_client()
        if client is None:
            return await self._fallback.dequeue(timeout=timeout)

        result = await client.brpop(self.queue_name, timeout=int(timeout))
        if result is None:
            return None
        _, raw = result
        data = json.loads(raw)
        msg_id = data.pop("_msg_id", f"rmsg-{int(time.time()*1000)}")
        return Message(msg_id=msg_id, payload=data, enqueued_at=time.time(), attempts=1)

    async def ack(self, msg_id: str) -> None:
        pass  # Redis BRPOP is destructive - message removed on dequeue

    async def nack(self, msg_id: str, error: str) -> None:
        client = await self._get_client()
        if client is None:
            return await self._fallback.nack(msg_id, error)
        # Move to DLQ
        await client.lpush(self.dlq_name, json.dumps({"msg_id": msg_id, "error": error}))

    async def queue_size(self) -> int:
        client = await self._get_client()
        if client is None:
            return await self._fallback.queue_size()
        return await client.llen(self.queue_name)


def create_queue(backend: str = "memory", **kwargs) -> BaseQueue:
    """Factory function for queue backends."""
    if backend == "redis":
        return RedisQueue(**kwargs)
    elif backend == "memory":
        return InMemoryQueue(**kwargs)
    else:
        raise ValueError(f"Unknown queue backend: {backend}. Use 'redis' or 'memory'.")
