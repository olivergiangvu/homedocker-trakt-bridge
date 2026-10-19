"""RC3-I offline coverage of real catalog GET-only probe logic."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts/audit-v140-rc3-series-catalog-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3i_probe",PATH)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

CANON="tt44051354"
ALIAS="tt44094505"
SERIES_ID="series-from-live-nextup"


def episode(i, series=SERIES_ID):
    return {"Id":"episode-"+str(i),"Type":"Episode","SeriesId":series,
            "ParentIndexNumber":1,"IndexNumber":i}


def mock_reader(*,length=21,ignored_offset=False,total_override=None,
                extra_bad_item=False,mutable=False):
    records=[episode(i) for i in range(1,length+1)]
    if extra_bad_item: records[0]["SeriesId"]="OTHER"
    reads=[]
    def read(path):
        reads.append(path)
        assert path.startswith("/Shows/")
        assert "StartIndex=" in path and "Limit=100" in path
        index=int(path.split("StartIndex=")[1].split("&")[0])
        if ignored_offset:index=0
        found=records[index:index+100]
        return {"Items":found,"TotalRecordCount":(
            total_override if total_override is not None else length),
            "StartIndex":index,
            "Count":len(found)}
    return read, reads


class SeriesCatalogTests(unittest.TestCase):
    def test_one_page_catalog_with_21_items_is_real(self):
        read, requests=mock_reader()
        items,pages=audit.scan_pages(read,SERIES_ID)
        self.assertEqual(len(items),21)
        self.assertEqual(len(pages),1)
        self.assertEqual(len(requests),1)
        self.assertEqual(items[0]["Id"],"episode-1")

    def test_two_pages_with_101_items_are_contiguous(self):
        read, requests=mock_reader(length=101)
        items,pages=audit.scan_pages(read,SERIES_ID)
        self.assertEqual(len(items),101)
        self.assertEqual(len(pages),2)
        self.assertIn("StartIndex=100",requests[1])

    def test_nextup_ignored_offset_not_accepted_in_catalog(self):
        read,_=mock_reader(length=101,ignored_offset=True)
        with self.assertRaisesRegex(
            audit.core.StopAudit,"CATALOG_PAGING_CONTRACT_INVALID"
        ):
            audit.scan_pages(read,SERIES_ID)

    def test_truncated_catalog_cannot_claim_full_count(self):
        read,_=mock_reader(length=101,total_override=202)
        with self.assertRaisesRegex(
            audit.core.StopAudit,"CATALOG_PARTIAL_TOTAL"
        ):
            audit.scan_pages(read,SERIES_ID)

    def test_over_400_items_fails_budget_not_fake_total(self):
        read,_=mock_reader(length=405)
        with self.assertRaisesRegex(
            audit.core.StopAudit,"CATALOG_EXCEEDS_AUDIT_BUDGET"
        ):
            audit.scan_pages(read,SERIES_ID)

    def test_cross_source_episode_id_fails_closed(self):
        read,_=mock_reader(extra_bad_item=True)
        with self.assertRaisesRegex(
            audit.core.StopAudit,"CATALOG_EPISODE_IDENTITY_INVALID"
        ):
            audit.scan_pages(read,SERIES_ID)

    def test_only_real_canonical_card_selected(self):
        rows=[{"SeriesId":"canon","Id":"live-id","Type":"Episode"}]
        candidate=audit.candidate_from_window(rows,{"canon":CANON},[
            [CANON,ALIAS]
        ])
        self.assertEqual(candidate["Id"],"live-id")
        with self.assertRaisesRegex(
            audit.core.StopAudit,"CANONICAL_ONLY_NEXTUP_NOT_PROVEN"
        ):
            audit.candidate_from_window(
                rows+[{"SeriesId":"alias","Id":"old","Type":"Episode"}],
                {"canon":CANON,"alias":ALIAS},[[CANON,ALIAS]]
            )

    def test_safe_stop_codes_never_echo_untrusted_exception_data(self):
        safe = audit.core.StopAudit("CATALOG_EPISODE_IDENTITY_INVALID")
        self.assertEqual(audit.safe_stop_code(safe),
                         "CATALOG_EPISODE_IDENTITY_INVALID")
        # An upstream response/exception might contain bearer/URLs/IDs.
        # Such text must NEVER be printed, even if it comes from StopAudit.
        untrusted = audit.core.StopAudit("bearer token example with HTTP body")
        self.assertEqual(audit.safe_stop_code(untrusted),
                         "UNCLASSIFIED_SAFE_STOP")
        self.assertEqual(audit.safe_stop_code(ValueError("secret")),
                         "LOCAL_RUNTIME_FAILURE")
        self.assertIn("CATALOG_FIRST_PASS", audit.PHASES)

    def test_main_marks_phase_before_authenticated_probe_error(self):
        fake = audit.core.StopAudit("AIO_GET_NON_200")
        with patch.object(audit.core, "credential",
                          return_value=("private", "mapped")), \
             patch.object(audit.core, "bridge_gate", return_value=True), \\
             patch.object(audit.visibility, "verified_groups_from_bridge",
                          return_value=[[CANON,ALIAS]]), \\
             patch.object(audit.core, "make_reader",
                          return_value=lambda path: (_ for _ in ()).throw(fake)):
            with self.assertRaises(audit.core.StopAudit):
                audit.main()
        self.assertEqual(audit.PHASE, "AUTHENTICATED_USER_PROBE")

    def test_secrets_stay_unprinted_and_no_mutation(self):
        source=PATH.read_text()
        for phrase in ["core.credential()","core.bridge_gate()",
                       '"/Users/Me"',"ThreadPoolExecutor(max_workers=4)",
                       "RC3I_SECRETS_PRINTED=NO"]:
            self.assertIn(phrase,source)
        for forbidden in ["docker compose","UPDATE watch_state","DELETE FROM watch_state",
                          "TraktClient","/PlaybackInfo","method='POST'"]:
            # /PlaybackInfo occurs only in a comment, ensure no HTTP request.
            if forbidden=="/PlaybackInfo":
                self.assertNotIn('read("/Items/"+quote(nextup_ep,safe="")+"/PlaybackInfo")',source)
            else:
                self.assertNotIn(forbidden,source)


if __name__=="__main__":
    unittest.main()
