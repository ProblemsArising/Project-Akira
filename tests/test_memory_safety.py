from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ai import memory


class MemorySafetyTests(unittest.TestCase):
    def settings(self):
        return SimpleNamespace(
            memory=SimpleNamespace(
                file="unused.json",
                max_turns=300,
                max_facts=200,
                max_context_chars=2500,
                recent_turns=0,
                relevant_limit=10,
            )
        )

    def test_remember_is_only_a_directive_at_statement_start(self):
        self.assertEqual(
            memory._extract_fact_candidates(
                "Of course in the end you won't remember anything... sorry"
            ),
            [],
        )
        self.assertEqual(
            memory._extract_fact_candidates(
                "Remember that the codeword is pineapple."
            ),
            ["The codeword is pineapple"],
        )

    def test_reported_meta_negation_does_not_create_false_preference(self):
        text = (
            "sorry its not like I like using you this way, its just the easiest "
            "to develop. of course in the end you wont remember anything... sorry"
        )
        self.assertEqual(memory._extract_fact_candidates(text), [])

    def test_direct_codeword_statement_is_saved(self):
        self.assertEqual(
            memory._extract_fact_candidates("codeword pineapple"),
            ["The codeword is pineapple"],
        )

    def test_negative_preference_is_preserved(self):
        self.assertEqual(
            memory._extract_fact_candidates("I don't like onions."),
            ["I don't like onions"],
        )

    def test_stable_preference_and_goal_are_extracted(self):
        self.assertEqual(
            memory._extract_fact_candidates(
                "I prefer local models. I want to build Akira into a companion."
            ),
            [
                "I prefer local models",
                "I want to build Akira into a companion",
            ],
        )

    def test_questions_and_temporary_states_are_not_promoted(self):
        self.assertEqual(
            memory._extract_fact_candidates(
                "Do I like local models? I'm tired. I was just testing backends."
            ),
            [],
        )

    def test_legacy_auto_facts_are_quarantined_and_backed_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            path.write_text(
                json.dumps(
                    {
                        "facts": [
                            {
                                "text": "anything... sorry",
                                "source": "auto",
                                "created_at": "2026-07-22T02:10:12",
                                "last_seen": "2026-07-22T02:10:12",
                                "times_seen": 1,
                            },
                            {
                                "text": "The user prefers local models",
                                "source": "manual",
                                "created_at": "2026-07-22T02:10:12",
                                "last_seen": "2026-07-22T02:10:12",
                                "times_seen": 1,
                            },
                        ],
                        "turns": [],
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(memory, "_memory_file", return_value=path), mock.patch.object(
                memory, "get_settings", return_value=self.settings()
            ):
                loaded = memory.load_memory()
                context = memory.build_memory_context("local models")

            self.assertEqual(loaded["schema_version"], 2)
            self.assertEqual(loaded["facts"][0]["status"], "review")
            self.assertEqual(loaded["facts"][1]["status"], "active")
            self.assertNotIn("anything... sorry", context)
            self.assertIn("The user prefers local models", context)
            self.assertEqual(
                len(list(path.parent.glob("memories.pre-memory-safety-*.json"))),
                1,
            )

    def test_remember_turn_stores_auditable_fact_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            with mock.patch.object(memory, "_memory_file", return_value=path), mock.patch.object(
                memory, "get_settings", return_value=self.settings()
            ):
                memory.remember_turn("codeword pineapple", "Got it")
                saved = memory.load_memory()

            fact = saved["facts"][0]
            self.assertEqual(fact["text"], "The codeword is pineapple")
            self.assertEqual(fact["kind"], "reference")
            self.assertEqual(fact["status"], "active")
            self.assertEqual(fact["extraction_version"], 2)
            self.assertEqual(fact["source_text"], "codeword pineapple")
            self.assertGreaterEqual(fact["confidence"], 0.9)


if __name__ == "__main__":
    unittest.main()
