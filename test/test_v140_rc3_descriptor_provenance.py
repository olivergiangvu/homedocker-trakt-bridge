"""RC3-N6 modeled series descriptor provenance, exact anchor & read-only."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/"scripts/audit-v140-rc3-descriptor-provenance-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3n6_descriptors",PATH)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture(n=205):
    keys=["s|tt"+str(70000000+i) for i in range(n)]
    recent=[(x,n-i) for i,x in enumerate(keys)]
    anchors=[(key,None,"series",n-i,1,1,"e|anchor"+str(i))
             for i,key in enumerate(keys)]
    descriptor=[
        ("e|anchor"+str(i),key[2:],
         key[2:]+":1:1","series",key,n-i,1,1)
        for i,key in enumerate(keys)]
    return {"recent":recent},{"anchors":anchors,"drops":[]},descriptor


class DescriptorTests(unittest.TestCase):
    def test_pinned_series_id_of_keeps_parseable_base(self):
        for base in ("tt12345678","kitsu:33","tmdb:123","tvdb:333"):
            with self.subTest(base=base):
                x,how=audit.effective_series_base(
                    base,"tvdb:999:1:2","series"
                )
                self.assertEqual(x,base)
                self.assertEqual(how,"RAW_BASE")

    def test_video_derived_series_only_if_token_boundary(self):
        cases=[
            ("catalog-tt12345678-watch","tt12345678:1:2",
             "tt12345678","VIDEO_TOKEN_DERIVED"),
            ("catalogtt12345678watch","tt12345678:1:2",
             "catalogtt12345678watch","RAW_BASE"),
            ("series-kitsu:33-other","kitsu:33:2",
             "kitsu:33","VIDEO_TOKEN_DERIVED"),
            ("catalog-alias","random:1:1","catalog-alias","RAW_BASE"),
            ("opaque-series",None,"opaque-series","RAW_BASE"),
        ]
        for base,video,expected,how in cases:
            with self.subTest(base=base):
                self.assertEqual(audit.effective_series_base(
                    base,video,"series"),(expected,how))

    def test_real_raw_source_equal_descriptor_for_205_packed_keys(self):
        index,anchors,rows=fixture()
        output=audit.classify(index,anchors,rows)
        self.assertEqual(
            output["TOP_200"]["ROUTED_BASE_HAS_A1_CANONICAL_SYNTAX_HINT"],
            200)
        self.assertEqual(
            output["BEYOND_200"]["ROUTED_BASE_HAS_A1_CANONICAL_SYNTAX_HINT"],
            5)
        self.assertEqual(
            output["TOP_200"]["ROUTED_BASE_EQUALS_SERIES_KEY"],200)
        self.assertEqual(
            output["BEYOND_200"]["VIDEO_TOKEN_DERIVED_BASE"],0)

    def test_validated_raw_source_vs_effective_descriptor_can_differ(self):
        index,anchors,rows=fixture(2)
        key=index["recent"][0][0]
        anchors["anchors"][0]=(
            key,"e|tt70000000:1:1","series",2,1,1,"e|anchor0")
        rows[0]=("e|anchor0","alias-tt70000000-other",
                 "tt70000000:1:1","series",key,2,1,1)
        out=audit.classify(index,anchors,rows)["TOP_200"]
        self.assertEqual(out["SOURCE_BASE_DIFFERS_FROM_SERIES_KEY"],1)
        self.assertEqual(out["ROUTED_BASE_EQUALS_SERIES_KEY"],2)
        self.assertEqual(out["VIDEO_TOKEN_DERIVED_BASE"],1)
        self.assertEqual(out["ROUTED_BASE_HAS_A1_CANONICAL_SYNTAX_HINT"],2)

    def test_hard_stop_if_anchor_changed_between_source_and_selected_row(self):
        index,anchors,rows=fixture(3)
        rows[0]=(*rows[0][:5],999,*rows[0][6:])
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"DESCRIPTOR_ANCHOR_MISMATCH"
        ):
            audit.classify(index,anchors,rows)

    def test_duplicate_or_missing_item_never_counted_as_real_series(self):
        index,anchors,rows=fixture(3)
        anchors["anchors"][1]=(
            *anchors["anchors"][1][:6],anchors["anchors"][0][6])
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"ANCHOR_ITEM_KEY_NOT_UNIQUE"
        ):
            audit.classify(index,anchors,rows)
        index,anchors,rows=fixture(3)
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"DESCRIPTOR_ROW_COUNT_MISMATCH"
        ):
            audit.classify(index,anchors,rows[:-1])

    def test_selected_item_keys_read_from_exact_blank_persona_sqlite_only(self):
        db=sqlite3.connect(":memory:")
        try:
            db.execute("""CREATE TABLE watch_state(
                uuid TEXT, persona TEXT,item_key TEXT,
                base_id TEXT,video_id TEXT,media_type TEXT,series_key TEXT,
                sort_at INTEGER,season INTEGER,episode INTEGER)""")
            db.executemany(
                "INSERT INTO watch_state VALUES(?,?,?,?,?,?,?,?,?,?)",[
                    ("mapped","","e|a","tt12345678","tt12345678:1:1",
                     "series","s|tt12345678",10,1,1),
                    ("mapped","other","e|a","tvdb:999","tvdb:999:1:1",
                     "series","s|tvdb:999",999,1,1),
                    ("other","","e|a","tmdb:999","tmdb:999:1:1",
                     "series","s|tmdb:999",999,1,1),
                ])
            db.commit()
            db.execute("PRAGMA query_only=ON")
            with patch.object(audit.identity,"ro",return_value=db):
                rows=audit.descriptor_snapshot(("mapped",""),["e|a"])
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0][1],"tt12345678")
        finally:
            db.close()

    def test_no_id_generation_no_metadata_no_user_history_writes(self):
        src=PATH.read_text()
        for required in (
            "n2.source_gate_and_anchors()",
            "identity.ro(identity.AIO_DB)",
            'reader("/Users/Me")',
            "shadow.bridge_gate()",
            "RC3N6_JELLYFIN_IDS_COMPUTED=0",
            "RC3N6_CATALOG_CALLS=0",
            "RC3N6_GLOBAL_NEXTUP_QUALIFIED=NO",
            "RC3N6_PRODUCTION_CHANGES=0",
        ):
            self.assertIn(required,src)
        for forbidden in (
            'reader("/Items/', 'reader("/Shows/', 'reader("/PlaybackInfo',
            'encodeItemId(', "DELETE FROM", "UPDATE watch_state",
            "INSERT INTO watch_state","docker compose","TraktClient",
            "print(rows)","print(anchors)","print(base)","print(video)",
        ):
            self.assertNotIn(forbidden,src)


if __name__=="__main__":
    unittest.main()
