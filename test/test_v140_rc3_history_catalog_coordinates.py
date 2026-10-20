"""RC3-K offline history/catalog coordinate matching and safety gates."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/audit-v140-rc3-history-catalog-coordinates-homedocker.py"
spec = importlib.util.spec_from_file_location("rc3k_join", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

C = "tt44051354"
A = "tt44094505"
SPELLINGS = [C, A]


def catalog_12():
    return [
        {"Type":"Episode","Id":"actual-"+str(n),"SeriesId":"series-test",
         "ParentIndexNumber":1,"IndexNumber":n}
        for n in range(1,13)
    ]


def source_19():
    return (
        [("s|"+C,1,n,1,0) for n in range(1,12)] +
        [("s|"+A,1,n,1,0) for n in range(1,9)]
    )


class HistoryCatalogTests(unittest.TestCase):
    def test_11_logical_history_coordinates_from_19_rows_match_12_catalog_items(self):
        state=audit.history_coordinates(source_19(),SPELLINGS)
        result=audit.match_catalog(catalog_12(),state)
        self.assertEqual(state["raw_rows"],19)
        self.assertEqual(result["canonical_rows"],11)
        self.assertEqual(result["alias_rows"],8)
        self.assertEqual(result["canonical_alias_overlap"],8)
        self.assertEqual(result["history_rows"],11)
        self.assertEqual(result["catalog_rows"],12)
        self.assertEqual(result["history_in_catalog"],11)
        self.assertEqual(result["history_missing_from_catalog"],0)
        self.assertEqual(result["catalog_without_history"],1)
        self.assertEqual(result["active_resumes"],0)
        self.assertEqual(result["alias_only"],0)

    def test_history_catalog_mismatch_stays_unqualified(self):
        source=audit.history_coordinates(source_19(),SPELLINGS)
        catalog=catalog_12()
        catalog[10]["IndexNumber"]=14
        result=audit.match_catalog(catalog,source)
        self.assertEqual(result["history_in_catalog"],10)
        self.assertEqual(result["history_missing_from_catalog"],1)
        self.assertEqual(result["catalog_without_history"],2)

    def test_overlap_played_conflict_is_not_silently_merged(self):
        rows=source_19()
        rows[-1]=("s|"+A,1,8,0,0)
        with self.assertRaisesRegex(
            audit.core.StopAudit,"AMBIGUOUS_PLAYBACK_STATE"
        ):
            audit.history_coordinates(rows,SPELLINGS)

    def test_two_resumes_are_not_moved_to_canonical(self):
        rows=[("s|"+C,1,5,0,1350),("s|"+A,1,5,0,999)]
        with self.assertRaisesRegex(
            audit.core.StopAudit,"AMBIGUOUS_PLAYBACK_STATE"
        ):
            audit.history_coordinates(rows,SPELLINGS)

    def test_one_alias_only_coordinate_is_preserved_as_history(self):
        rows=[("s|"+C,1,3,1,0),("s|"+A,1,4,0,250)]
        result=audit.history_coordinates(rows,SPELLINGS)
        self.assertEqual(result["alias_only"],1)
        self.assertEqual(result["active_resumes"],1)
        self.assertEqual(len(result["logical_coords"]),2)

    def test_duplicate_season_episode_per_spelling_never_collapses(self):
        rows=[("s|"+C,1,4,1,0),("s|"+C,1,4,1,0)]
        with self.assertRaisesRegex(
            audit.core.StopAudit,"DUPLICATE_WATCH_COORDINATE"
        ):
            audit.history_coordinates(rows,SPELLINGS)

    def test_invalid_or_unverified_state_rejected(self):
        for invalid in [
            ("s|tt99999999",1,1,1,0),
            ("s|"+C,-1,1,1,0),
            ("s|"+C,1,0,1,0),
            ("s|"+C,1,1,1,-2),
            ("s|"+C,1,1,2,0),
            ("s|"+C,1,1,1,10),
        ]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(audit.core.StopAudit):
                    audit.history_coordinates([invalid],SPELLINGS)

    def test_duplicate_catalog_coordinate_rejected(self):
        source=audit.history_coordinates(source_19(),SPELLINGS)
        rows=catalog_12()
        rows[11]["IndexNumber"]=11
        with self.assertRaisesRegex(
            audit.core.StopAudit,"CATALOG_COORDINATE_DUPLICATE"
        ):
            audit.match_catalog(rows,source)

    def test_never_write_or_print_identifier_and_no_catalog_synthesis(self):
        src=PATH.read_text()
        for need in [
            "identity.ro(identity.AIO_DB)",
            "identity.ro(identity.BRIDGE_DB)",
            "core.credential()",
            "core.bridge_gate()",
            "read_bounded_nextup",
            "scan_pages",
            "core.make_reader",
            "RC3K_PRODUCTION_CHANGES=0",
            "RC3K_SECRETS_PRINTED=NO",
            "RC3K_GLOBAL_NEXTUP_QUALIFIED=NO",
        ]:
            self.assertIn(need,src)
        for forbidden in [
            "DELETE FROM", "INSERT INTO", "UPDATE watch_state",
            "POST", "/PlaybackInfo", "docker compose", "cacheSet(",
        ]:
            self.assertNotIn(forbidden,src)
        self.assertNotIn('print("RC3K_COORDINATE=',src)
        self.assertNotIn("print(rows)",src)


if __name__=="__main__":
    unittest.main()
