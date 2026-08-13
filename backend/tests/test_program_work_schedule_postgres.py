from __future__ import annotations

import os
import unittest
import uuid

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from failures import DiracInvalidParameters
from programs.repository import PostgresProgramRepository


@unittest.skipUnless(os.environ.get("DIRAC_TEST_DSN") and psycopg,
                     "requires isolated PostgreSQL DIRAC_TEST_DSN")
class PostgresProgramWorkScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connect = staticmethod(lambda: psycopg.connect(os.environ["DIRAC_TEST_DSN"]))
        cls.repo = PostgresProgramRepository(cls.connect)
        cls.actor = {"kind": "human", "id": "schedule-test"}

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        created = self.repo.create({"code": f"SCHEDULE-{suffix}", "name": "Schedule test"},
                                   self.actor, f"create-{suffix}")
        self.program_ref = created["program"]["ref"]

    def test_schedule_round_trip_and_cycle_refusal(self) -> None:
        first = self.repo.record_work_package(self.program_ref, 1, {
            "key": "understand", "title": "Understand", "description": "Map the context",
            "start_on": "2026-08-13", "due_on": "2026-08-18",
        }, self.actor, "work-understand")
        second = self.repo.record_work_package(self.program_ref, 2, {
            "key": "design", "title": "Design", "description": "Create proposals",
            "lane": "design", "start_on": "2026-08-17", "due_on": "2026-08-25",
            "depends_on_refs": [first["work_item"]["ref"]],
        }, self.actor, "work-design")
        overview = self.repo.get(self.program_ref)["program"]
        design = next(item for item in overview["work_items"] if item["key"] == "design")
        self.assertEqual((design["start_on"], design["due_on"]),
                         ("2026-08-17", "2026-08-25"))
        self.assertEqual(design["depends_on_refs"], [first["work_item"]["ref"]])
        with self.assertRaises(DiracInvalidParameters):
            self.repo.record_work_package(self.program_ref, 3, {
                "key": "understand", "title": "Understand", "description": "Cycle",
                "depends_on_refs": [second["work_item"]["ref"]],
            }, self.actor, "cycle")
        self.assertEqual(self.repo.get(self.program_ref)["program"]["version"], 3)

    def test_database_and_domain_reject_reversed_dates(self) -> None:
        with self.assertRaises(DiracInvalidParameters):
            self.repo.record_work_package(self.program_ref, 1, {
                "key": "reversed", "title": "Reversed", "description": "Invalid dates",
                "start_on": "2026-08-20", "due_on": "2026-08-19",
            }, self.actor, "reversed")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO design.program_work_item("
                        "program_id,work_key,title,created_by_kind,created_by_id) "
                        "VALUES (%s,'invalid-raw','Invalid raw item','human','test') RETURNING id",
                        (self.program_ref["id"],))
            work_item_id = cur.fetchone()[0]
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute("INSERT INTO design.program_work_package("
                            "program_id,work_item_id,work_key,revision,title,description,start_on,due_on,"
                            "created_by_kind,created_by_id) VALUES (%s,%s,"
                            "'invalid-raw',1,'Invalid','Invalid','2026-08-20','2026-08-19','human','test')",
                            (self.program_ref["id"], work_item_id))


if __name__ == "__main__":
    unittest.main()
