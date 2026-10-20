"""Release vertical slice: upstream a1 vector and live backend validation gates."""
import importlib.util
from pathlib import Path
import unittest

FILE=(Path(__file__).resolve().parents[1]/
      "scripts/shadow-v140-offwindow-nextup-slice-homedocker.py")
spec=importlib.util.spec_from_file_location("r5_slice",FILE)
subject=importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)
IMDB="tt12345678"
SID="a12102000000bc614effffffff000000"
EP="a14102000000bc614e00010001000000"

class SliceTests(unittest.TestCase):
    def test_exact_16_byte_pinned_a1_series_vector_and_media_code(self):
        self.assertEqual(subject.packed_series_id(IMDB,"series"),SID)
        self.assertEqual(subject.packed_series_id(IMDB,"anime")[4:6],"03")
        self.assertEqual(subject.packed_series_id(IMDB,"tv")[4:6],"04")
        self.assertEqual(len(bytes.fromhex(SID)),16)
        self.assertEqual(bytes.fromhex(SID)[9:13],b"\xff"*4)
        self.assertEqual(bytes.fromhex(SID)[13:],b"\x00"*3)
        self.assertIsNone(subject.packed_series_id("tt00000001","series"))
        self.assertIsNone(subject.packed_series_id(IMDB,"movie"))
        self.assertIsNone(subject.packed_series_id("kitsu:222","anime"))
        self.assertIsNone(subject.packed_series_id("tt281474976710656","series"))

    def test_server_must_validate_source_imdb_before_any_nextup_call(self):
        routes=[]
        def bad(path,missing_ok=False):
            routes.append(path)
            return {"Type":"Series","Id":SID,"ProviderIds":{"Imdb":"tt99999999"}}
        self.assertIsNone(subject.try_episode(bad,IMDB,SID))
        self.assertEqual(routes,["/Items/"+SID])

    def test_404_is_not_an_existing_server_series(self):
        routes=[]
        def missing(path,missing_ok=False):
            routes.append((path,missing_ok))
            return None
        self.assertIsNone(subject.try_episode(missing,IMDB,SID))
        self.assertEqual(routes,[("/Items/"+SID,True)])

    def test_real_backend_series_id_to_nextup_catalog_episode(self):
        routes=[]
        def get(path,missing_ok=False):
            routes.append(path)
            if path=="/Items/"+SID:
                return {"Type":"Series","Id":SID,
                        "ProviderIds":{"Imdb":IMDB}}
            if path.startswith("/Shows/NextUp?"):
                return {"Items":[{"Type":"Episode","Id":EP,
                                  "SeriesId":SID,"ParentIndexNumber":1,
                                  "UserData":{"Played":False}}]}
            if path.startswith("/Shows/"+SID+"/Episodes?"):
                return {"StartIndex":0,"TotalRecordCount":1,
                        "Items":[{"Type":"Episode","Id":EP,"SeriesId":SID}]}
            if path=="/Items/"+EP:
                return {"Type":"Episode","Id":EP,"SeriesId":SID}
            raise AssertionError("Unexpected endpoint")
        self.assertEqual(subject.try_episode(get,IMDB,SID),1)
        self.assertEqual(len(routes),4)
        self.assertIn("Season=1",routes[2])

    def test_no_nextup_or_missing_catalog_fails_closed(self):
        def empty(path,missing_ok=False):
            if path=="/Items/"+SID:
                return {"Type":"Series","Id":SID,
                        "ProviderIds":{"Imdb":IMDB}}
            if path.startswith("/Shows/NextUp?"):
                return {"Items":[]}
            raise AssertionError("no other GET allowed")
        self.assertIsNone(subject.try_episode(empty,IMDB,SID))
        def failed(path,missing_ok=False):
            if path=="/Items/"+SID:
                return {"Type":"Series","Id":SID,"ProviderIds":{"Imdb":IMDB}}
            if path.startswith("/Shows/NextUp?"):
                return {"Items":[{"Type":"Episode","Id":EP,
                                  "SeriesId":SID,
                                  "UserData":{"Played":False}}]}
            if "/Episodes?" in path:
                return {"StartIndex":0,"TotalRecordCount":0,"Items":[]}
            raise AssertionError("should stop")
        with self.assertRaisesRegex(subject.StopSlice,"NO_VALID_EPISODE_CATALOG"):
            subject.try_episode(failed,IMDB,SID)

    def test_wrong_client_user_played_or_ambiguous_nextup_fails(self):
        def made(played):
            def get(path,missing_ok=False):
                if path=="/Items/"+SID:
                    return {"Type":"Series","Id":SID,"ProviderIds":{"Imdb":IMDB}}
                if "/NextUp?" in path:
                    return {"Items":[{"Type":"Episode","Id":EP,
                                      "SeriesId":SID,
                                      "UserData":{"Played":played}}]}
                raise AssertionError("should not call catalog")
            return get
        for v in (True,None):
            with self.assertRaisesRegex(
                subject.StopSlice,"NO_OFFWINDOW_NEXTUP_VERIFIED"):
                subject.try_episode(made(v),IMDB,SID)

    def test_outside_top_200_only_three_no_match_redirects(self):
        keys=["s|tt"+str(70000000+i) for i in range(208)]
        index={"recent":[(k,208-i) for i,k in enumerate(keys)]}
        anchors=[(key,None,"series",208-i,1,1,"e|"+str(i))
                 for i,key in enumerate(keys)]
        anchors[201]=(keys[201],"e|tt70000000:1:1","series",7,1,1,"e|201")
        selected=subject.select_candidates(index,{"anchors":anchors})
        self.assertEqual(len(selected),3)
        expected=[keys[200],keys[202],keys[203]]
        self.assertEqual([x[0] for x in selected],[k[2:] for k in expected])
        self.assertTrue(all(subject.JF_ID.fullmatch(x[1]) for x in selected))

    def test_no_id_guessing_b2_writes_or_production_imports(self):
        source=FILE.read_text()
        for phrase in (
            "UPDATE watch_state","DELETE FROM","INSERT INTO",
            "encodeItemId(", "Search/Hints?", "docker compose",
            "print(imdb)", "print(series_id)", "print(ep_id)",
            'get("/PlaybackInfo', "TraktClient", "gatewayFilteringEnabled(",
        ):
            self.assertNotIn(phrase,source)
        for phrase in (
            "SOURCE.source_gate_and_anchors()",
            'get("/Users/Me")',
            "SOURCE.shadow.bridge_gate()",
            "MAX_SAMPLES=3","MAX_GETS=10",
            "R5SLICE_FILTER_FINAL=OFF",
            "R5SLICE_PRODUCTION_CHANGES=0",
        ):
            self.assertIn(phrase,source)

if __name__=="__main__":
    unittest.main()
