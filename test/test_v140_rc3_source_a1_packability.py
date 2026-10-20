"""RC3-N5: canonical numeric a1 syntax hints are NOT issued Jellyfin IDs."""
import importlib.util
from pathlib import Path
import unittest

PATH=Path(__file__).resolve().parents[1]/"scripts/audit-v140-rc3-source-a1-packability-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3n5_a1_hints",PATH)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture():
    bases=["tt12345678","kitsu:109","tvdb:903","tmdb:99",
           "tt00000001","kitsu:000109","tvdb:281474976710656",
           "opaque-source"]
    media=["series","anime","tv","series","series","anime","tv","series"]
    index={"recent":[("s|"+base,8-i) for i,base in enumerate(bases)]}
    anchors=[]
    for i,(base,typ) in enumerate(zip(bases,media)):
        matched=("e|tt12345678:1:2" if i==1 else None)
        anchors.append(("s|"+base,matched,typ,8-i,1,1,"e|row-"+str(i)))
    return index,{"anchors":anchors,"drops":[]}


class A1HintsTests(unittest.TestCase):
    def test_pinned_parser_numeric_rebuild_canonicality(self):
        examples={
            ("s|tt12345678","series"):True,
            ("s|tt0123456","series"):True,
            ("s|tt00000001","series"):False,
            ("s|tmdb:12","series"):True,
            ("s|tvdb:999","tv"):True,
            ("s|kitsu:109","anime"):True,
            ("s|kitsu:0109","anime"):False,
            ("s|mal:123","anime"):True,
            ("s|anilist:123","anime"):True,
            ("s|anidb:123","anime"):True,
            ("s|simkl:123","series"):True,
            ("s|tvdb:281474976710655","tv"):True,
            ("s|tvdb:281474976710656","tv"):False,
            ("s|imdb:123","series"):False,
            ("s|opaque","series"):False,
            ("s|tt1000000","unsupported"):False,
        }
        for item,expected in examples.items():
            with self.subTest(item=item):
                classification=audit.packability_hint(*item)
                self.assertEqual(
                    classification=="CANONICAL_PACKABLE_SYNTAX",expected)
        self.assertEqual(audit.packability_hint(
            "s|tt12345678","unsupported"),"MEDIA_TYPE_UNSUPPORTED")

    def test_source_ranked_bands_and_cross_namespace_redirect_only_hint(self):
        idx,anc=fixture()
        stats=audit.count_hints(idx,anc)
        band=stats["TOP_200"]
        self.assertEqual(band["RAW_SOURCE_KEYS"],8)
        self.assertEqual(band["CANONICAL_PACKABLE_SYNTAX_HINT"],4)
        self.assertEqual(band["NONCANONICAL_OR_OPAQUE_SOURCE"],4)
        self.assertEqual(band["REDIRECT_TARGET_CANONICAL_PACKABLE_SYNTAX_HINT"],1)
        self.assertEqual(band["REDIRECT_TARGET_UNPACKABLE_OR_UNVERIFIED"],0)
        self.assertEqual(stats["BEYOND_200"]["RAW_SOURCE_KEYS"],0)
        self.assertEqual(stats["BEYOND_200"]["CANONICAL_PACKABLE_SYNTAX_HINT"],0)

    def test_205_candidates_keep_off_window_results_separate(self):
        idx,anc=fixture()
        keys=[f"s|tt{70000000+i}" for i in range(205)]
        idx={"recent":[(k,205-i) for i,k in enumerate(keys)]}
        anc={"anchors":[(k,None,"series",205-i,1,1,"e|row-"+str(i))
                        for i,k in enumerate(keys)],"drops":[]}
        out=audit.count_hints(idx,anc)
        self.assertEqual(out["TOP_200"]["RAW_SOURCE_KEYS"],200)
        self.assertEqual(out["TOP_200"]["CANONICAL_PACKABLE_SYNTAX_HINT"],200)
        self.assertEqual(out["BEYOND_200"]["RAW_SOURCE_KEYS"],5)
        self.assertEqual(out["BEYOND_200"]["CANONICAL_PACKABLE_SYNTAX_HINT"],5)

    def test_refuse_duplicate_or_mismatched_source_anchors(self):
        idx,anc=fixture()
        anc["anchors"].pop()
        with self.assertRaises(audit.shadow.StopAudit):
            audit.count_hints(idx,anc)

    def test_never_computes_or_exports_a1_or_b2_and_does_not_call_catalog(self):
        source=PATH.read_text()
        for needed in ("n2.source_gate_and_anchors()",
                       "n2.summarize(index,anchor_data)",
                       'reader("/Users/Me")',
                       "RC3N5_JELLYFIN_IDS_COMPUTED=0",
                       "RC3N5_GLOBAL_NEXTUP_QUALIFIED=NO",
                       "RC3N5_PRODUCTION_CHANGES=0"):
            self.assertIn(needed,source)
        for forbidden in ('reader("/Items/','reader("/Shows/',
                          'reader("/PlaybackInfo','encodeItemId(',
                          "UPDATE watch_state","INSERT INTO watch_state",
                          "DELETE FROM watch_state","docker compose",
                          "print(index)","print(anchors)","print(raw_key)"):
            self.assertNotIn(forbidden,source)


if __name__=="__main__":
    unittest.main()
