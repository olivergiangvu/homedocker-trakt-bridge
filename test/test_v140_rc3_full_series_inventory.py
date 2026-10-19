"""Offline regressions for RC3-M full scoped series-key discovery."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import sqlite3

PATH=Path(__file__).resolve().parents[1]/"scripts/audit-v140-rc3-full-series-inventory-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3m_full_inventory",PATH)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

CANON="tt44051354"
ALIAS="tt44094505"


def fake_snapshot(count=861,extra_inactive=0):
    names=["s|"+CANON,"s|"+ALIAS]+[
        "s|tt"+str(70000000+i) for i in range(count-2)
    ]
    history=[(key,11 if i==0 else 8 if i==1 else 1)
             for i,key in enumerate(names)]
    history.extend(("s|tt"+str(80000000+i),1)
                   for i in range(extra_inactive))
    recent=[(key,count-i) for i,key in enumerate(names)]
    return {
        "history":history,"recent":recent,
        "graph_show_count":17,"groups":[[CANON,ALIAS]],
    }


class InventoryTests(unittest.TestCase):
    def test_full_861_candidates_includes_the_661_outside_200(self):
        counts=audit.aggregate(fake_snapshot())
        self.assertEqual(counts["full_history_series_keys"],861)
        self.assertEqual(counts["recent_series_candidate_keys"],861)
        self.assertEqual(counts["history_only_series_keys"],0)
        self.assertEqual(counts["source_candidate_rank_1_to_50"],50)
        self.assertEqual(counts["source_candidate_rank_51_to_200"],150)
        self.assertEqual(counts["source_candidate_rank_beyond_200"],661)
        self.assertEqual(counts["verified_alias_spelling_reduction_hint"],1)
        self.assertEqual(counts["inventory_hint_count"],860)

    def test_history_rows_without_watched_or_resume_stay_outside_ranked(self):
        counts=audit.aggregate(fake_snapshot(40,2))
        self.assertEqual(counts["history_only_series_keys"],2)
        self.assertEqual(counts["recent_series_candidate_keys"],40)
        self.assertEqual(counts["source_candidate_rank_beyond_200"],0)

    def test_opaque_keys_counted_not_dropped_or_named_identity(self):
        sample=fake_snapshot(3)
        sample["history"].append(("s|opaque-provider-item",1))
        sample["recent"].append(("s|opaque-provider-item",0))
        counts=audit.aggregate(sample)
        self.assertEqual(counts["full_history_series_keys"],4)
        self.assertEqual(counts["history_keys_other_source_syntax"],1)

    def test_candidate_subset_violation_fails_closed(self):
        sample=fake_snapshot(3)
        sample["recent"].append(("s|tt99999999",0))
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"SOURCE_RECENT_NOT_IN_HISTORY"
        ):
            audit.aggregate(sample)

    def test_duplicate_history_key_is_rejected(self):
        sample=fake_snapshot(3)
        sample["history"].append(sample["history"][0])
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"SOURCE_HISTORY_COUNT_INVALID"
        ):
            audit.aggregate(sample)

    def test_verified_groups_must_not_collide(self):
        sample=fake_snapshot(3)
        sample["groups"]=[[CANON,ALIAS],[CANON,"tt90000000"]]
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"GRAPH_UNTRUSTED"
        ):
            audit.aggregate(sample)

    def test_bad_order_or_unsupported_sort_at_fails_closed(self):
        sample=fake_snapshot(3)
        sample["recent"][1]=(sample["recent"][1][0],9999)
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"SOURCE_RECENT_ORDER_INVALID"
        ):
            audit.aggregate(sample)
        sample=fake_snapshot(3)
        sample["recent"][1]=(sample["recent"][1][0],None)
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"SOURCE_RECENT_SORT_INVALID"
        ):
            audit.aggregate(sample)

    def test_actual_sqlite_aggregate_predicate_includes_zero_position_rows(self):
        # All queries are SQLite read-only in deployment; use temp in-memory
        # fixtures here to confirm the intended grouping/predicate exactly.
        db=sqlite3.connect(":memory:")
        try:
            db.execute("""CREATE TABLE watch_state(
              uuid TEXT,persona TEXT,series_key TEXT,
              episode INTEGER,played INTEGER,position_ms REAL,
              sort_at REAL)""")
            rows=[
                ("one","p","s|tt11111111",1,1,0,20),
                ("one","p","s|tt11111111",2,0,0,3),
                ("one","p","s|tt22222222",1,0,0,5),
                ("one","p","s|tt33333333",1,0,100,10),
                ("other","p","s|tt44444444",1,1,0,1000),
                ("one","other","s|tt55555555",1,1,0,1000),
            ]
            db.executemany(
                "INSERT INTO watch_state VALUES(?,?,?,?,?,?,?)",rows)
            db.commit()
            db.execute("PRAGMA query_only=ON")
            history=db.execute(
                """SELECT series_key,COUNT(*) FROM watch_state
                 WHERE uuid=? AND persona=? AND series_key IS NOT NULL
                 AND episode IS NOT NULL GROUP BY series_key ORDER BY series_key""",
                ("one","p")).fetchall()
            candidate=db.execute(
                """SELECT series_key,MAX(sort_at) FROM watch_state
                   WHERE uuid=? AND persona=? AND series_key IS NOT NULL
                   AND episode IS NOT NULL AND (played=1 OR position_ms>0)
                   GROUP BY series_key ORDER BY MAX(sort_at) DESC,series_key""",
                ("one","p")).fetchall()
            self.assertEqual(history,[
                ("s|tt11111111",2),("s|tt22222222",1),("s|tt33333333",1)])
            self.assertEqual([x[0] for x in candidate],
                             ["s|tt11111111","s|tt33333333"])
        finally:
            db.close()

    def test_source_uses_only_readonly_and_no_raw_identifier_output(self):
        src=PATH.read_text()
        for need in [
            "identity.ro(identity.AIO_DB)",
            "identity.ro(identity.BRIDGE_DB)",
            "aio.execute(\"BEGIN\")",
            "bridge.execute(\"BEGIN\")",
            "AND (played=1 OR position_ms>0)",
            "shadow.bridge_gate()",
            "shadow.credential()",
            "reader(\"/Users/Me\")",
            "RC3M_SOURCE_RANK_NOT_NEXTUP_CARD=YES",
            "RC3M_SECRETS_PRINTED=NO",
        ]:
            self.assertIn(need,src)
        for unsafe in [
            "UPDATE watch_state","INSERT INTO","DELETE FROM",
            "/PlaybackInfo","method=\"POST\"","docker compose",
            "print(hist)", "print(candidates)", "print(scope)",
            "print(graph)",
        ]:
            self.assertNotIn(unsafe,src)


if __name__=="__main__":
    unittest.main()
