"""Release Recovery: real server-issued off-window ID E2E shadow contract."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

FILE=(Path(__file__).resolve().parents[1]/
      "scripts/shadow-v140-offwindow-nextup-slice-homedocker.py")
spec=importlib.util.spec_from_file_location("release_slice",FILE)
slice=importlib.util.module_from_spec(spec)
spec.loader.exec_module(slice)

SERIES="a1"+"0"*30
EPISODE="a1"+"1"*30
IMDB="tt12345678"


class SliceTests(unittest.TestCase):
    def test_one_real_server_series_episode_catalog_end_to_end(self):
        calls=[]
        responses={
            "/Search/Hints?SearchTerm=Example+Series&Limit=5&IncludeItemTypes=Series":
                {"SearchHints":[{"Type":"Series","ItemId":SERIES}]},
            "/Items/"+SERIES:
                {"Type":"Series","Id":SERIES,"ProviderIds":{"Imdb":IMDB}},
            "/Shows/NextUp?SeriesId="+SERIES+"&Limit=1":
                {"Items":[{"Type":"Episode","Id":EPISODE,
                           "SeriesId":SERIES,"UserData":{"Played":False}}]},
            "/Shows/"+SERIES+"/Episodes?StartIndex=0&Limit=100":
                {"StartIndex":0,"TotalRecordCount":1,"Items":[
                    {"Type":"Episode","Id":EPISODE,"SeriesId":SERIES}]},
            "/Items/"+EPISODE:
                {"Type":"Episode","Id":EPISODE,"SeriesId":SERIES},
        }
        def read(path):
            calls.append(path)
            return responses[path]
        result=slice.verify_search_and_nextup(read,IMDB,"Example Series")
        self.assertTrue(result["series_verified"])
        self.assertTrue(result["episode_verified"])
        self.assertEqual(result["catalog_episode_count"],1)
        self.assertEqual(len(calls),5)
        self.assertTrue(all(("/Items/" in x or "/Search/" in x or
                             "/Shows/" in x) for x in calls))

    def test_never_accept_same_title_wrong_provider_imdb(self):
        def read(path):
            if path.startswith("/Search/"):
                return {"SearchHints":[{"Type":"Series","ItemId":SERIES}]}
            return {"Type":"Series","Id":SERIES,
                    "ProviderIds":{"Imdb":"tt99999999"}}
        with self.assertRaisesRegex(
            slice.StopSlice,"SERIES_IDENTITY_UNVERIFIED"
        ):
            slice.verify_search_and_nextup(read,IMDB,"Example Series")

    def test_no_episode_or_no_catalog_match_is_stopped(self):
        paths=[]
        def read(path):
            paths.append(path)
            if path.startswith("/Search/"):
                return {"SearchHints":[{"Type":"Series","ItemId":SERIES}]}
            if path=="/Items/"+SERIES:
                return {"Type":"Series","Id":SERIES,
                        "ProviderIds":{"Imdb":IMDB}}
            if "/NextUp?" in path:
                return {"Items":[]}
            raise AssertionError("catalog should not be read")
        with self.assertRaisesRegex(
            slice.StopSlice,"NEXTUP_NO_QUALIFIED_EPISODE"
        ):
            slice.verify_search_and_nextup(read,IMDB,"Example Series")
        self.assertEqual(len(paths),3)

    def test_catalog_missing_real_episode_must_fail_closed(self):
        def read(path):
            if path.startswith("/Search/"):
                return {"SearchHints":[{"Type":"Series","ItemId":SERIES}]}
            if path=="/Items/"+SERIES:
                return {"Type":"Series","Id":SERIES,
                        "ProviderIds":{"Imdb":IMDB}}
            if "/NextUp?" in path:
                return {"Items":[{"Type":"Episode","Id":EPISODE,
                                  "SeriesId":SERIES}]}
            if "/Episodes?" in path:
                return {"Items":[],"TotalRecordCount":0,"StartIndex":0}
            raise AssertionError("should stop before episode detail")
        with self.assertRaisesRegex(
            slice.StopSlice,"NEXTUP_EPISODE_NOT_IN_CATALOG"
        ):
            slice.verify_search_and_nextup(read,IMDB,"Example Series")

    def test_source_selector_never_draws_from_first_200_or_redirects(self):
        keys=["s|tt"+str(70000000+i) for i in range(205)]
        recent=[(k,205-i) for i,k in enumerate(keys)]
        anchor=[(k,None,"series",205-i,1,1,"e|test-"+str(i))
                for i,k in enumerate(keys)]
        anchor[201]=(keys[201],"e|tt70000000:1:2","series",
                     4,1,1,"e|test-201")
        selected=slice.take_candidates({"recent":recent},
                                       {"anchors":anchor})
        self.assertEqual(selected[0][0],201)
        self.assertEqual(selected[1][0],203)
        self.assertTrue(all(x[0]>200 and x[2][1] is None for x in selected))

    def test_real_sqlite_selection_uses_exact_mapped_scope_and_title(self):
        keys=["s|tt"+str(70000000+i) for i in range(202)]
        recent=[(k,202-i) for i,k in enumerate(keys)]
        anchors=[(k,None,"series",202-i,1,1,"e|"+str(i))
                 for i,k in enumerate(keys)]
        db=sqlite3.connect(":memory:")
        db.execute("CREATE TABLE watch_state(uuid TEXT,persona TEXT,"
                   "item_key TEXT,series_key TEXT,sort_at INTEGER,"
                   "media_type TEXT,snapshot TEXT)")
        for rank in (200,201):
            k=keys[rank]
            db.execute("INSERT INTO watch_state VALUES(?,?,?,?,?,?,?)",
                       ("mapped","", "e|"+str(rank),k,202-rank,
                        "series",'{"seriesName":"Example Series"}'))
        db.execute("INSERT INTO watch_state VALUES(?,?,?,?,?,?,?)",
                   ("different","", "e|200",keys[200],999,
                    "series",'{"seriesName":"Wrong User"}'))
        db.commit()
        db.execute("PRAGMA query_only=ON")
        class FakeIdentity:
            AIO_DB="known"
            @staticmethod
            def ro(path): return db
        class FakeN2:
            identity=FakeIdentity()
        found=slice.selected_snapshot(FakeN2(),{"recent":recent,"scope":("mapped","")},
                                      {"anchors":anchors})
        self.assertEqual(found[0],201)
        self.assertEqual(found[3],"Example Series")
        db.close()

    def test_recovery_branch_has_no_routing_writes_or_synthetic_ids(self):
        src=FILE.read_text()
        for forbidden in (
            "INSERT INTO", "UPDATE watch_state","DELETE FROM",
            "encodeItemId","base64.urlsafe_b64encode",
            "docker compose","gatewayFilteringEnabled(",
            "print(title)", "print(key)", "print(token)",
            "print(sid)", "print(eid)","print(issued)",
            "print(responses)",
        ):
            self.assertNotIn(forbidden,src)
        for required in (
            "Search/Hints?","/Shows/NextUp?","/Episodes?",
            "CALLS>=MAX_HTTP","NoRedirect()","ProxyHandler({})",
            "shadow.bridge_gate()", "SOURCE_RANK_OUTSIDE_200",
            "R5SLICE_PRODUCTION_CHANGES=0",
        ):
            self.assertIn(required,src)


if __name__=="__main__":
    unittest.main()
