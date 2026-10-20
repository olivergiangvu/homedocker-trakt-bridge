"""RC3-N1 offline ID namespace coverage and source provenance tests."""
import importlib.util
from pathlib import Path
import unittest

PATH=Path(__file__).resolve().parents[1]/"scripts/audit-v140-rc3-source-namespaces-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3n1_namespace",PATH)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def snapshot_906():
    # Recreates only the observed aggregate source shape, not user IDs.
    imdb=["s|tt"+str(70000000+i) for i in range(756)]
    other=(
        ["s|tvdb:"+str(100000+i) for i in range(90)] +
        ["s|tmdb:"+str(100000+i) for i in range(40)] +
        ["s|kitsu:"+str(10000+i) for i in range(20)]
    )
    # 756+150=906 history keys; 711+150=861 recent candidates.
    recent=(imdb[:711]+other)
    return {
        "history":[(x,1) for x in imdb+other],
        "recent":[(x,861-i) for i,x in enumerate(recent)],
        "groups":[[imdb[0][2:],imdb[1][2:]]],
        "graph_show_count":17,
    }


class NamespaceTests(unittest.TestCase):
    def test_common_aio_idparser_syntax_distinct_no_unverified_joins(self):
        cases={
            "s|tt12345678":"IMDB_CANONICAL",
            "s|tt123":"IMDB_ALTERNATE",
            "s|imdb:12345678":"IMDB_ALTERNATE",
            "s|tvdb:12345":"TVDB",
            "s|tvdb12345":"TVDB",
            "s|tmdb:120":"TMDB",
            "s|mal:120":"MAL",
            "s|kitsu:120":"KITSU",
            "s|anilist:120":"ANILIST",
            "s|anidb:120":"ANIDB",
            "s|ap:120":"ANIME_MISC",
            "s|acd:120":"ANIME_MISC",
            "s|notifymoe:ABCdef12":"ANIME_MISC",
            "s|simkl:120":"ANIME_MISC",
            "s|ttbad":"OTHER_OR_OPAQUE",
            "s|other:120":"OTHER_OR_OPAQUE",
            "no-series-prefix":"OTHER_OR_OPAQUE",
            None:"OTHER_OR_OPAQUE",
        }
        for key,want in cases.items():
            with self.subTest(key=key):
                self.assertEqual(audit.kind(key),want)

    def test_actual_906_861_shape_is_aggregate_only(self):
        snap=snapshot_906()
        hist,recent,late,source=audit.summarize(snap)
        self.assertEqual(source["full_history_series_keys"],906)
        self.assertEqual(source["recent_series_candidate_keys"],861)
        self.assertEqual(source["source_candidate_rank_beyond_200"],661)
        self.assertEqual(hist["IMDB_CANONICAL"],756)
        self.assertEqual(recent["IMDB_CANONICAL"],711)
        self.assertEqual(hist["TVDB"],90)
        self.assertEqual(hist["TMDB"],40)
        self.assertEqual(hist["KITSU"],20)
        self.assertEqual(sum(hist.values()),906)
        self.assertEqual(sum(recent.values()),861)
        self.assertEqual(sum(late.values()),661)

    def test_source_recent_ranking_not_an_authenticated_playable_shelf(self):
        snap=snapshot_906()
        _,recent,late,_=audit.summarize(snap)
        self.assertTrue(recent["TVDB"]>0)
        self.assertTrue(late["TVDB"]>0)
        self.assertTrue(late["IMDB_CANONICAL"]>0)
        self.assertEqual(sum(late.values()),661)

    def test_missing_recent_key_or_inventory_totals_mismatch_fail_closed(self):
        snap=snapshot_906()
        snap["recent"][-1]=("s|tt999999999999",0)
        with self.assertRaises(audit.shadow.StopAudit):
            audit.summarize(snap)

    def test_history_with_zero_candidate_keeps_namespace_count(self):
        snap={
            "history":[("s|tt12345678",1),("s|tvdb:12",1)],
            "recent":[("s|tt12345678",100)],
            "groups":[],"graph_show_count":0,
        }
        h,r,l,_=audit.summarize(snap)
        self.assertEqual(h["TVDB"],1)
        self.assertEqual(r["TVDB"],0)
        self.assertEqual(sum(l.values()),0)

    def test_guard_does_not_contact_series_catalog_or_print_ids(self):
        source=PATH.read_text()
        for required in [
            "inventory.snapshot()", "inventory.aggregate(first)",
            "shadow.credential()", "shadow.bridge_gate()",
            'read("/Users/Me")',"RC3N1_RESOLVABLE_ITEM_IDS_PROVEN=NO",
            "RC3N1_GLOBAL_NEXTUP_QUALIFIED=NO",
            "RC3N1_PRODUCTION_CHANGES=0",
        ]:
            self.assertIn(required,source)
        for forbidden in [
            "UPDATE watch_state","DELETE FROM","INSERT INTO",
            "method='POST'","docker compose",
            "print(snapshot)","print(first)","print(recent)",
        ]:
            self.assertNotIn(forbidden,source)
        # Mentions of upstream routes in a docstring are permitted. A
        # genuine route invocation with source-derived IDs is NOT.
        self.assertNotIn('read("/Shows/',source)
        self.assertNotIn('read("/Items/',source)
        self.assertNotIn('read("/PlaybackInfo',source)


if __name__=="__main__":
    unittest.main()
