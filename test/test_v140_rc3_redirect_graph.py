"""RC3-N3 safe redirect-graph evidence tests; no API or writes."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit-v140-rc3-redirect-graph-homedocker.py"
spec=importlib.util.spec_from_file_location("rc3n3_graph",SCRIPT)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def case(links,n=10):
    keys=["s|tt"+str(70000000+i) for i in range(n)]
    index={
        "history":[(key,1) for key in keys],
        "recent":[(key,n-i) for i,key in enumerate(keys)],
    }
    anchor=[]
    for i,key in enumerate(keys):
        target=links.get(i)
        match="e|"+keys[target][2:]+":1:1" if target is not None else None
        anchor.append((key,match,"series",n-i,1,1,"e|anchor-"+str(i)))
    return index,{"anchors":anchor,"drops":[]}


class GraphTests(unittest.TestCase):
    def test_cycle_multihop_and_fanin_are_not_marked_equivalence(self):
        # 0->1->2->0 cycle; 3->2 gives multi-inbound; 4->5 self is ignored.
        index,data=case({0:1,1:2,2:0,3:2,4:4})
        m=audit.graph_metrics(index,data)
        self.assertEqual(m["SOURCE_REDIRECT_EDGES"],4)
        self.assertEqual(m["PARSEABLE_SELF_MATCH"],1)
        self.assertEqual(m["REDIRECT_EDGE_TARGET_IN_RECENT"],4)
        self.assertEqual(m["REDIRECT_DISTINCT_DESTINATIONS"],3)
        self.assertEqual(m["DESTINATIONS_WITH_MULTIPLE_SOURCES"],1)
        self.assertEqual(m["EDGES_TARGETING_MULTISOURCE_DESTINATIONS"],2)
        self.assertEqual(m["MAX_SOURCES_TO_ONE_DESTINATION"],2)
        self.assertEqual(m["CYCLE_COMPONENTS"],1)
        self.assertEqual(m["CYCLE_NODES"],3)
        self.assertEqual(m["SOURCES_REACHING_A_CYCLE"],4)
        self.assertGreaterEqual(m["MULTIHOP_STARTS"],3)

    def test_linear_redirect_chain_has_no_cycles(self):
        index,data=case({0:1,1:2,2:3},5)
        m=audit.graph_metrics(index,data)
        self.assertEqual(m["SOURCE_REDIRECT_EDGES"],3)
        self.assertEqual(m["CYCLE_COMPONENTS"],0)
        self.assertEqual(m["MULTIHOP_STARTS"],2)
        self.assertEqual(m["MAX_REDIRECT_HOPS"],3)

    def test_no_matching_redirects_keeps_graph_empty(self):
        index,data=case({},3)
        m=audit.graph_metrics(index,data)
        self.assertEqual(m["SOURCE_REDIRECT_EDGES"],0)
        self.assertEqual(m["CYCLE_COMPONENTS"],0)
        self.assertEqual(m["PARSEABLE_SELF_MATCH"],0)
        self.assertEqual(m["MAX_REDIRECT_HOPS"],0)

    def test_target_present_history_but_not_recent_is_not_promoted(self):
        index,data=case({0:2},3)
        index["recent"]=index["recent"][:2]
        data["anchors"]=data["anchors"][:2]
        m=audit.graph_metrics(index,data)
        self.assertEqual(m["REDIRECT_EDGE_TARGET_HISTORY_ONLY"],1)
        self.assertEqual(m["REDIRECT_EDGE_TARGET_IN_RECENT"],0)
        self.assertEqual(m["REDIRECT_EDGE_TARGET_NOT_IN_HISTORY"],0)

    def test_external_target_never_constructed_as_a_series_item(self):
        index,data=case({},3)
        anchor=list(data["anchors"][0])
        anchor[1]="e|tvdb:9999:1:2"
        data["anchors"][0]=tuple(anchor)
        m=audit.graph_metrics(index,data)
        self.assertEqual(m["REDIRECT_EDGE_TARGET_NOT_IN_HISTORY"],1)
        self.assertEqual(m["REDIRECT_CROSS_NAMESPACE"],1)
        self.assertEqual(m["REDIRECT_EDGE_TARGET_IN_RECENT"],0)

    def test_off_window_118_are_not_synthetic_nextup_cards(self):
        index,data=case({},861)
        keys=[row[0] for row in index["recent"]]
        for pos in range(200,318):
            row=list(data["anchors"][pos])
            row[1]="e|"+keys[0][2:]+":1:5"
            data["anchors"][pos]=tuple(row)
        m=audit.graph_metrics(index,data)
        self.assertEqual(m["SOURCE_REDIRECT_EDGES"],118)
        self.assertEqual(m["TOP_200_REDIRECT_EDGES"],0)
        self.assertEqual(m["BEYOND_200_REDIRECT_EDGES"],118)
        self.assertEqual(m["REDIRECT_EDGE_TARGET_TOP_200"],118)
        self.assertEqual(m["MAX_SOURCES_TO_ONE_DESTINATION"],118)
        self.assertEqual(m["CYCLE_COMPONENTS"],0)

    def test_any_missing_anchor_fails_closed(self):
        index,data=case({},3)
        data["anchors"].pop()
        with self.assertRaises(audit.shadow.StopAudit):
            audit.graph_metrics(index,data)

    def test_readonly_source_not_wired_to_production(self):
        s=SCRIPT.read_text()
        for needed in (
            "source_gate_and_anchors()","shadow.credential()",
            'read("/Users/Me")',"shadow.bridge_gate()",
            "RC3N3_SQLITE=MODE_RO_QUERY_ONLY",
            "RC3N3_CATALOG_CALLS=0","RC3N3_PRODUCTION_CHANGES=0",
        ):
            self.assertIn(needed,s)
        for forbidden in (
            'read("/Items/', 'read("/Shows/', 'read("/PlaybackInfo',
            "UPDATE watch_state","INSERT INTO watch_state",
            "DELETE FROM watch_state", "docker compose",
            "TraktClient","createServer(",
        ):
            self.assertNotIn(forbidden,s)
        self.assertNotIn("print(index)",s)
        self.assertNotIn("print(extra)",s)
        self.assertNotIn("print(edges)",s)


if __name__=="__main__":
    unittest.main()
