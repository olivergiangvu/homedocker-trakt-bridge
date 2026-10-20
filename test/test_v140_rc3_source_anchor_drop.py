"""RC3-N2 source-only anchor/drop shadow tests: never generate Jellyfin IDs."""
from pathlib import Path
import importlib.util
import sqlite3
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit-v140-rc3-source-anchor-drop-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3n2",SCRIPT)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture(n=204):
    keys=["s|tt"+str(70000000+i) for i in range(n)]
    index={
        "recent":[(k,n-i) for i,k in enumerate(keys)]
    }
    anchors=[]
    for i,key in enumerate(keys):
        match=None
        if i==0: match="e|"+key[2:]+":1:2"
        if i==1: match="e|kitsu:113:4"
        if i==201: match="e|"+keys[0][2:]+":1:2"
        anchors.append((key,match,"series",n-i,1,i+1,"e|private-item-"+str(i)))
    drops=[(keys[0],None,n+1)]
    return index,{"anchors":anchors,"drops":drops}


class AnchorDropTests(unittest.TestCase):
    def test_supported_match_syntax_is_never_jellyfin_id(self):
        self.assertEqual(audit.match_series_key("e|tt12345678:1:12"),
                         "s|tt12345678")
        self.assertEqual(audit.match_series_key("e|tvdb:8913:1:1"),
                         "s|tvdb:8913")
        self.assertEqual(audit.match_series_key("e|tmdb:88:1:1"),
                         "s|tmdb:88")
        self.assertEqual(audit.match_series_key("e|kitsu:13:53"),
                         "s|kitsu:13")
        self.assertEqual(audit.match_series_key("e|mal:13:53"),
                         "s|mal:13")
        self.assertIsNone(audit.match_series_key("e|opaque-alias"))
        self.assertIsNone(audit.match_series_key("https://private"))
        self.assertIsNone(audit.match_series_key(None))

    def test_exact_raw_anchor_count_and_drop_hint(self):
        index,data=fixture()
        result,drops=audit.summarize(index,data)
        self.assertEqual(drops,1)
        self.assertEqual(result["TOP_200"]["SOURCE_CANDIDATE_KEYS"],200)
        self.assertEqual(result["BEYOND_200"]["SOURCE_CANDIDATE_KEYS"],4)
        self.assertEqual(result["TOP_200"]["ANCHOR_PARSEABLE_EPISODE_MATCH"],2)
        self.assertEqual(result["BEYOND_200"]["ANCHOR_PARSEABLE_EPISODE_MATCH"],1)
        self.assertEqual(result["TOP_200"]["EFFECTIVE_DROP_HINT"],1)
        # The beyond-200 anchor points syntactically at top candidate 1,
        # whose effective drop is newer, so the source-level hint is 1.
        self.assertEqual(result["BEYOND_200"]["EFFECTIVE_DROP_HINT"],1)
        self.assertEqual(
            result["BEYOND_200"]["SOURCE_KEY_DIFFERS_FROM_PARSED_MATCH"],1)
        self.assertEqual(
            result["BEYOND_200"]["MATCH_DESTINATION_HAS_SOURCE_CANDIDATE"],1)

    def test_drop_more_recent_than_anchor_only(self):
        index,data=fixture(3)
        data["drops"]=[(index["recent"][0][0],None,3)]
        result,_=audit.summarize(index,data)
        self.assertEqual(result["TOP_200"]["EFFECTIVE_DROP_HINT"],0)
        data["drops"]=[(index["recent"][0][0],None,4)]
        result,_=audit.summarize(index,data)
        self.assertEqual(result["TOP_200"]["EFFECTIVE_DROP_HINT"],1)

    def test_never_guess_missing_source_anchors(self):
        index,data=fixture(3)
        data["anchors"].pop()
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"ANCHOR_COUNT_UNVERIFIED"
        ):
            audit.summarize(index,data)

    def test_mismatch_sort_at_is_unqualified(self):
        index,data=fixture(4)
        data["anchors"][0]=(
            *data["anchors"][0][:3],99,*data["anchors"][0][4:])
        with self.assertRaisesRegex(audit.shadow.StopAudit,"SOURCE_RANK_INVALID"):
            audit.summarize(index,data)

    def test_null_and_numeric_string_drop_timestamps_match_upstream(self):
        index,data=fixture(3)
        data["drops"]=[(index["recent"][0][0],None,None)]
        result,_=audit.summarize(index,data)
        self.assertEqual(result["TOP_200"]["EFFECTIVE_DROP_HINT"],0)
        data["drops"]=[(index["recent"][0][0],None,"4")]
        result,_=audit.summarize(index,data)
        self.assertEqual(result["TOP_200"]["EFFECTIVE_DROP_HINT"],1)
        data["drops"]=[(index["recent"][0][0],None,"invalid-hidden-value")]
        with self.assertRaisesRegex(
            audit.shadow.StopAudit,"DROP_ROW_INVALID"
        ):
            audit.summarize(index,data)

    def test_actual_sqlite_anchor_query_scoped_and_query_only(self):
        db=sqlite3.connect(":memory:")
        db.executescript("""
          CREATE TABLE watch_state (
            uuid TEXT, persona TEXT, series_key TEXT,
            match_key TEXT, media_type TEXT, sort_at INTEGER,
            season INTEGER, episode INTEGER, item_key TEXT,
            played INTEGER, position_ms INTEGER,
            dropped INTEGER, dropped_at INTEGER
          );
        """)
        db.executemany(
            "INSERT INTO watch_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",[
                ("correct","","s|tt12345678",None,"series",50,1,1,
                 "e|item1",1,0,0,None),
                ("correct","","s|tt12345678","e|tt12345678:1:2","series",
                 50,1,2,"e|item2",1,0,0,None),
                ("correct","","s|tvdb:55",None,"series",30,1,1,
                 "e|item3",0,100,0,None),
                ("correct","other-persona","s|tt99999999",None,
                 "series",999,1,1,"e|cross-persona",1,0,0,None),
                ("wrong","","s|tt88888888",None,
                 "series",999,1,1,"e|cross-user",1,0,0,None),
                ("correct","","s|tt12345678",None,
                 "series",0,None,None,"s|tt12345678",0,0,1,100),
            ])
        db.commit()
        db.execute("PRAGMA query_only=ON")
        with patch.object(audit.identity,"ro",return_value=db):
            found=audit.anchor_snapshot(("correct",""))
        self.assertEqual(len(found["anchors"]),2)
        self.assertEqual(len(found["drops"]),1)
        self.assertEqual(found["anchors"][0][1],"e|tt12345678:1:2")
        self.assertTrue(all("99999999" not in a[0] for a in found["anchors"]))
        self.assertTrue(all("88888888" not in a[0] for a in found["anchors"]))

    def test_runtime_script_has_no_writes_or_metadata_storm(self):
        source=SCRIPT.read_text()
        for s in ["identity.ro(identity.AIO_DB)",
                  'reader("/Users/Me")',
                  "inventory.snapshot()",
                  "source_gate_and_anchors()",
                  "RC3N2_JELLYFIN_ID_GENERATION=NONE",
                  "RC3N2_CATALOG_ENDPOINT_CALLS=0",
                  "RC3N2_SECRETS_PRINTED=NO"]:
            self.assertIn(s,source)
        for s in ["UPDATE watch_state","INSERT INTO watch_state",
                  "DELETE FROM watch_state",
                  'reader("/Shows/', 'reader("/Items/',"docker compose",
                  "TraktClient","createServer("]:
            self.assertNotIn(s,source)
        # A docstring can name the forbidden endpoint. No real API calls.
        self.assertNotIn('reader("/PlaybackInfo',source)
        self.assertNotIn('reader("/Items/',source)


if __name__=="__main__":
    unittest.main()
