#!/usr/bin/env python3
"""Tests for mcp_agent.task_store.TaskStore."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from mcp_agent.task_store import TaskStore


class TestTaskStore(unittest.TestCase):
    """Unit tests for TaskStore."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_task_store_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_and_get_task(self):
        """Creating a task and reading it back returns the same record."""
        store = TaskStore(self.tmpdir)
        record = store.create_task(
            conversation_id="conv-1",
            prompt="Test prompt",
            idempotency_key=None,
        )
        self.assertIn("task_id", record)
        self.assertEqual(record["conversation_id"], "conv-1")
        self.assertEqual(record["prompt"], "Test prompt")
        self.assertEqual(record["status"], "queued")
        self.assertIsNotNone(record["created_at"])
        self.assertIsNotNone(record["updated_at"])

        retrieved = store.get_task(record["task_id"])
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["task_id"], record["task_id"])
        self.assertEqual(retrieved["conversation_id"], "conv-1")

    def test_get_unknown_task_returns_none(self):
        """Fetching a non-existent task_id returns None."""
        store = TaskStore(self.tmpdir)
        self.assertIsNone(store.get_task("nonexistent-task-id"))

    def test_update_task(self):
        """Updating task fields persists correctly."""
        store = TaskStore(self.tmpdir)
        record = store.create_task(
            conversation_id="conv-2",
            prompt="Update test",
        )
        tid = record["task_id"]

        updated = store.update_task(tid, status="running", conversation_id="conv-2-updated")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["conversation_id"], "conv-2-updated")

        # Verify persistence by re-loading.
        reloaded = store.get_task(tid)
        self.assertEqual(reloaded["status"], "running")
        self.assertEqual(reloaded["conversation_id"], "conv-2-updated")

    def test_find_by_conversation_id(self):
        """find_by_conversation_id returns the matching task."""
        store = TaskStore(self.tmpdir)
        store.create_task(conversation_id="conv-find", prompt="find me")
        store.create_task(conversation_id="conv-other", prompt="not this one")

        found = store.find_by_conversation_id("conv-find")
        self.assertIsNotNone(found)
        self.assertEqual(found["conversation_id"], "conv-find")

        # Non-existent conversation.
        self.assertIsNone(store.find_by_conversation_id("conv-does-not-exist"))

    def test_find_by_idempotency_key(self):
        """find_by_idempotency_key returns the matching task."""
        store = TaskStore(self.tmpdir)
        store.create_task(
            conversation_id="conv-idem",
            prompt="idem test",
            idempotency_key="key-abc",
        )

        found = store.find_by_idempotency_key("key-abc")
        self.assertIsNotNone(found)
        self.assertEqual(found["idempotency_key"], "key-abc")

        # Non-existent key.
        self.assertIsNone(store.find_by_idempotency_key("key-missing"))

    def test_list_tasks(self):
        """list_tasks returns all task_ids."""
        store = TaskStore(self.tmpdir)
        r1 = store.create_task(conversation_id="c1", prompt="p1")
        r2 = store.create_task(conversation_id="c2", prompt="p2")

        ids = store.list_tasks()
        self.assertIn(r1["task_id"], ids)
        self.assertIn(r2["task_id"], ids)
        self.assertEqual(len(ids), 2)

    def test_task_survives_reload(self):
        """Task state persists across TaskStore instances (file-based)."""
        store1 = TaskStore(self.tmpdir)
        record = store1.create_task(
            conversation_id="conv-persist",
            prompt="persist test",
            idempotency_key="persist-key",
        )
        tid = record["task_id"]

        # Simulate process restart by creating a new TaskStore.
        store2 = TaskStore(self.tmpdir)
        reloaded = store2.get_task(tid)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["conversation_id"], "conv-persist")
        self.assertEqual(reloaded["prompt"], "persist test")
        self.assertEqual(reloaded["idempotency_key"], "persist-key")

    def test_task_file_on_disk(self):
        """A JSON file is created on disk for each task."""
        store = TaskStore(self.tmpdir)
        record = store.create_task(conversation_id="conv-disk", prompt="disk test")
        tid = record["task_id"]

        filepath = os.path.join(self.tmpdir, f"{tid}.json")
        self.assertTrue(os.path.exists(filepath))

        data = json.loads(open(filepath).read())
        self.assertEqual(data["task_id"], tid)
        self.assertEqual(data["conversation_id"], "conv-disk")

    def test_concurrent_updates(self):
        """Concurrent updates do not corrupt state."""
        store = TaskStore(self.tmpdir)
        record = store.create_task(conversation_id="conv-concurrent", prompt="concurrent")
        tid = record["task_id"]

        errors = []

        def updater(n):
            try:
                for i in range(50):
                    store.update_task(tid, status=f"running-{n}-{i}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=updater, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors during concurrent update: {errors}")

        final = store.get_task(tid)
        self.assertIsNotNone(final)
        # Status should be one of the values set by the threads.
        self.assertTrue(final["status"].startswith("running-"))


if __name__ == "__main__":
    unittest.main()
