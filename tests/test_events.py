from __future__ import annotations

import asyncio
import threading
import unittest

from app.events import EventHub, EventStreamClosed


class EventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_from_worker_thread_reaches_async_subscriber(self) -> None:
        hub = EventHub()
        subscription = hub.subscribe()

        thread = threading.Thread(
            target=lambda: hub.publish("worker.event", {"value": 42})
        )
        thread.start()
        thread.join()

        event = await asyncio.wait_for(subscription.receive(), timeout=1.0)
        self.assertEqual(event["type"], "worker.event")
        self.assertEqual(event["data"], {"value": 42})
        self.assertEqual(event["sequence"], 1)
        self.assertTrue(event["timestamp"].endswith("Z"))

        subscription.close()
        self.assertEqual(hub.subscriber_count, 0)

    async def test_bounded_queue_keeps_newest_events(self) -> None:
        hub = EventHub(max_queue_size=2)
        subscription = hub.subscribe()

        hub.publish("event.one")
        hub.publish("event.two")
        hub.publish("event.three")

        first = await asyncio.wait_for(subscription.receive(), timeout=1.0)
        second = await asyncio.wait_for(subscription.receive(), timeout=1.0)
        self.assertEqual(first["type"], "event.two")
        self.assertEqual(second["type"], "event.three")

    async def test_close_wakes_subscribers(self) -> None:
        hub = EventHub()
        subscription = hub.subscribe()
        hub.close()

        with self.assertRaises(EventStreamClosed):
            await asyncio.wait_for(subscription.receive(), timeout=1.0)
        self.assertTrue(hub.closed)
        self.assertEqual(hub.subscriber_count, 0)

    async def test_event_sequences_increase_for_direct_and_broadcast_events(self) -> None:
        hub = EventHub()
        direct = hub.create_event("connection.ready")
        broadcast = hub.publish("status.changed")
        self.assertEqual(direct["sequence"], 1)
        self.assertEqual(broadcast["sequence"], 2)


if __name__ == "__main__":
    unittest.main()
