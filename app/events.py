"""Thread-safe WebSocket event broadcasting for Project Akira.

Conversation, microphone, TTS, and avatar work may run in normal Python worker
threads while FastAPI WebSocket connections live on an asyncio event loop.
``EventHub`` bridges those two worlds without making the core conversation
service depend on FastAPI.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

EventData = Mapping[str, Any]
EventPayload = dict[str, Any]
_EVENT_STREAM_CLOSED = object()


class EventStreamClosed(RuntimeError):
    """Raised when a subscription is read after the event hub closes."""


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """One event sent to WebUI clients."""

    sequence: int
    type: str
    timestamp: str
    data: dict[str, Any]

    def to_dict(self) -> EventPayload:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "timestamp": self.timestamp,
            "data": dict(self.data),
        }


class EventSubscription:
    """One bounded event queue owned by a WebSocket connection."""

    def __init__(
        self,
        hub: "EventHub",
        *,
        subscription_id: str,
        loop: asyncio.AbstractEventLoop,
        max_queue_size: int,
    ) -> None:
        self._hub = hub
        self.id = subscription_id
        self.loop = loop
        self._queue: asyncio.Queue[EventPayload | object] = asyncio.Queue(
            maxsize=max(1, int(max_queue_size))
        )
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def receive(self) -> EventPayload:
        item = await self._queue.get()
        if item is _EVENT_STREAM_CLOSED:
            raise EventStreamClosed("The Project Akira event stream is closed")
        return item  # type: ignore[return-value]

    async def send_direct(self, event: EventPayload) -> None:
        """Queue a response intended only for this connection."""

        self._enqueue(event)

    def close(self) -> None:
        self._hub.unsubscribe(self)

    def _enqueue(self, event: EventPayload) -> None:
        if self._closed:
            return

        # A slow or suspended browser must not grow memory without bounds.
        # Keep the newest state by dropping the oldest queued event.
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Another callback may have filled the queue between the check and
            # put. Dropping one non-critical live event is safer than blocking.
            pass

    def _close_from_hub(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(_EVENT_STREAM_CLOSED)
        except asyncio.QueueFull:
            pass


class EventHub:
    """Publish structured events safely from any Project Akira thread."""

    def __init__(self, *, max_queue_size: int = 256) -> None:
        self.max_queue_size = max(1, int(max_queue_size))
        self._lock = threading.RLock()
        self._subscriptions: dict[str, EventSubscription] = {}
        self._sequence = 0
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def create_event(
        self,
        event_type: str,
        data: EventData | None = None,
    ) -> EventPayload:
        normalized_type = str(event_type).strip()
        if not normalized_type:
            raise ValueError("event_type cannot be blank")

        with self._lock:
            self._sequence += 1
            sequence = self._sequence

        envelope = EventEnvelope(
            sequence=sequence,
            type=normalized_type,
            timestamp=datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            data=dict(data or {}),
        )
        return envelope.to_dict()

    def subscribe(self) -> EventSubscription:
        loop = asyncio.get_running_loop()
        subscription = EventSubscription(
            self,
            subscription_id=uuid.uuid4().hex,
            loop=loop,
            max_queue_size=self.max_queue_size,
        )

        with self._lock:
            if self._closed:
                raise EventStreamClosed("The Project Akira event hub is closed")
            self._subscriptions[subscription.id] = subscription
        return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        with self._lock:
            removed = self._subscriptions.pop(subscription.id, None)
        if removed is not None:
            removed._close_from_hub()

    def publish(
        self,
        event_type: str,
        data: EventData | None = None,
    ) -> EventPayload:
        event = self.create_event(event_type, data)
        with self._lock:
            if self._closed:
                return event
            subscriptions = tuple(self._subscriptions.values())

        for subscription in subscriptions:
            try:
                subscription.loop.call_soon_threadsafe(
                    subscription._enqueue,
                    event,
                )
            except RuntimeError:
                # The browser/event loop already closed. It will be removed by
                # the WebSocket endpoint's cleanup path.
                self.unsubscribe(subscription)
        return event

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = tuple(self._subscriptions.values())
            self._subscriptions.clear()

        for subscription in subscriptions:
            try:
                subscription.loop.call_soon_threadsafe(
                    subscription._close_from_hub
                )
            except RuntimeError:
                subscription._close_from_hub()
