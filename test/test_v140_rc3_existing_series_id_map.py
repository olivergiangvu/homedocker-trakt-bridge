"""RC3-N4: globally persisted hashed Jellyfin ID map is not user issuance."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit-v140-rc3-existing-series-id-map-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3n4_existing_map",SCRIPT)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def b2(i):
    return "b2"+format(i,"030x")


def payload(base,media="series",kind="series"):
    return json.dumps({"k":kind,"i":base,"t":media})


def sample(count=205):
    keys=["s|tt"+str(70000000+i) for i in range(count)]
    recent=[(key,count-i) for i,key in enumerate(keys)]
    idx={"recent":recent,"history":[(k,1) for k in keys]}
    anchors=[]
    for i,key in enumerate(keys):
        match=("e|tvdb:123:1:1" if i==201 else None)
        anchors.append((key,match,"series",count-i,1,1,"e|item-"+str(i)))
    return idx,{"anchors":anchors,"drops":[]}


class ExistingMapTests(unittest.TestCase):
    def test_global_b2_mapping_is_only_a_count_not_user_issued(self):
        index,anchor=sample()
        recent=index["recent"]
        rows=[
            (b2(1),payload(recent[0][0][2:]),100),
            (b2(2),payload(recent[201][0][2:]),101),
            (b2(3),payload("tvdb:123","tv"),102),
            (b2(4),payload("someone-else","anime"),103),
            (b2(5),payload("other","tv","episode"),104),
        ]
        counts=audit.summarize(index,anchor,rows)
        self.assertEqual(counts["GLOBAL_ID_MAP_TOTAL_ROWS"],5)
        self.assertEqual(counts["GLOBAL_HASHED_SERIES_DESCRIPTOR_BASES"],4)
        self.assertEqual(counts["GLOBAL_HASHED_SERIES_DESCRIPTOR_IDS"],4)
        self.assertEqual(counts["GLOBAL_ID_MAP_NON_SERIES_OR_UNEXPECTED_KIND_ROWS"],1)
        self.assertEqual(counts["HISTORY_SOURCE_KEYS_WITH_EXISTING_HASHED_SERIES_MAP"],2)
        self.assertEqual(counts["RECENT_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"],2)
        self.assertEqual(counts["TOP_200_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"],1)
        self.assertEqual(counts["BEYOND_200_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"],1)
        self.assertEqual(counts["REDIRECT_DISTINCT_TARGET_SOURCE_KEYS"],1)
        self.assertEqual(counts["REDIRECT_TARGETS_WITH_EXISTING_HASHED_SERIES_MAP"],1)
        self.assertEqual(counts["REDIRECT_TARGETS_OUTSIDE_HISTORY_WITH_MAP"],1)
        self.assertEqual(counts["RECENT_IMDB_CANONICAL_WITH_GLOBAL_HASHED_MAP"],2)

    def test_multiple_preexisting_global_ids_same_base_do_not_imply_identity(self):
        idx,anchor=sample(2)
        rows=[(b2(1),payload(idx["recent"][0][0][2:]),100),
              (b2(2),payload(idx["recent"][0][0][2:],"tv"),101)]
        out=audit.summarize(idx,anchor,rows)
        self.assertEqual(out["GLOBAL_HASHED_SERIES_DESCRIPTOR_BASES"],1)
        self.assertEqual(out["GLOBAL_HASHED_SERIES_DESCRIPTOR_IDS"],2)
        self.assertEqual(out["GLOBAL_HASHED_SERIES_BASES_WITH_MULTIPLE_IDS"],1)
        self.assertEqual(out["RECENT_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"],1)

    def test_packed_a1_or_unrecognized_id_is_not_a_stored_b2_candidate(self):
        idx,anchor=sample(2)
        rows=[("a1"+"0"*30,payload(idx["recent"][0][0][2:]),100)]
        out=audit.summarize(idx,anchor,rows)
        self.assertEqual(out["RECENT_CANDIDATES_WITH_EXISTING_HASHED_SERIES_MAP"],0)
        self.assertEqual(out["GLOBAL_ID_MAP_NON_SERIES_OR_UNEXPECTED_KIND_ROWS"],1)

    def test_invalid_payload_and_duplicate_id_rejected_without_secret_echo(self):
        rows=[(b2(1),"{invalid-secret",0)]
        with self.assertRaisesRegex(audit.shadow.StopAudit,"ID_MAP_DATA_INVALID"):
            audit.validate_map(rows)
        rows=[(b2(1),payload("tt12345678"),1),
              (b2(1),payload("tt12345679"),2)]
        with self.assertRaisesRegex(audit.shadow.StopAudit,"ID_MAP_DUPLICATE_ID"):
            audit.validate_map(rows)

    def test_readonly_global_sqlite_snapshot_uses_mapping_schema_exactly(self):
        db=sqlite3.connect(":memory:")
        db.execute("CREATE TABLE jellyfin_id_map (id TEXT PRIMARY KEY,payload TEXT,seen_at INTEGER)")
        db.execute("INSERT INTO jellyfin_id_map VALUES(?,?,?)",
                   (b2(1),payload("kitsu:3","anime"),500))
        db.commit()
        db.execute("PRAGMA query_only=ON")
        with patch.object(audit.identity,"ro",return_value=db):
            rows=audit.map_snapshot()
        self.assertEqual(len(rows),1)
        self.assertEqual(audit.validate_map(rows)[1],0)

    def test_hard_row_budget_stops_without_list_dump(self):
        db=sqlite3.connect(":memory:")
        db.execute("CREATE TABLE jellyfin_id_map(id TEXT,payload TEXT,seen_at INTEGER)")
        with patch.object(audit.identity,"ro",return_value=db):
            with patch.object(audit,"MAX_MAP_ROWS",-1):
                with self.assertRaisesRegex(
                    audit.shadow.StopAudit,"ID_MAP_ROWS_OVER_BUDGET"
                ):
                    audit.map_snapshot()

    def test_no_synthetic_ids_or_metadata_fanout(self):
        source=SCRIPT.read_text()
        for needed in (
            "n2.source_gate_and_anchors()","n2.summarize(index,anchors)",
            "identity.ro(identity.AIO_DB)",
            "RC3N4_PACKED_A1_IDS_NOT_IN_THIS_TABLE=YES",
            "RC3N4_ID_MAP_MEMBERSHIP_NOT_USER_ISSUANCE=YES",
            "RC3N4_SOURCE_TO_JELLYFIN_ID_RESOLVED=NO",
            "RC3N4_PRODUCTION_CHANGES=0",
        ):
            self.assertIn(needed,source)
        for forbidden in (
            "UPDATE watch_state","INSERT INTO watch_state",
            "DELETE FROM watch_state","DELETE FROM jellyfin_id_map",
            "UPDATE jellyfin_id_map","INSERT INTO jellyfin_id_map",
            'get("/Items/', 'get("/Shows/', 'get("/PlaybackInfo',
            "encodeItemId(", "TraktClient", "docker compose",
            "print(rows)", "print(by_base)", "print(anchors)",
        ):
            self.assertNotIn(forbidden,source)


if __name__=="__main__":
    unittest.main()
