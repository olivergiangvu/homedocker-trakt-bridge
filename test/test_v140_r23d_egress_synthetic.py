import importlib.util
import io
import json
from pathlib import Path
from unittest import TestCase, mock
from contextlib import redirect_stderr, redirect_stdout

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "probe-v140-r23d-egress-synthetic.py"
spec = importlib.util.spec_from_file_location("r23d1b_egress_probe", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
IMAGE = "sha256:" + "a"*64

class SyntheticEgressProbeTests(TestCase):
    def test_probe_has_no_secret_or_jellyfin_api_requests(self):
        code = mod.NODE_PROBE
        self.assertIn("node:net", code)
        self.assertIn("documentation", mod.__doc__.lower() if "documentation" in mod.__doc__.lower() else "documentation")
        for forbidden in ("X-Emby", "password", "AuthenticateByName", "fetch(", "axios",
                          "trakt.tv", "watch_state", "docker.sock", "media_core_net"):
            self.assertNotIn(forbidden, code)
        self.assertIn("192.0.2.1", code)
        self.assertIn("198.51.100.1", code)
        self.assertIn("203.0.113.1", code)

    def test_declared_probe_contract_rejects_any_other_shape(self):
        good = {
          "contract":"R23D1B_SYNTHETIC_TCP_ONLY",
          "positive_control_host_gateway": True,
          "host_reachable_port_bools":[False, False, True, False, False],
          "testnet_reachable_bools":[False, False, False],
          "probes_performed":9,
          "authentication_or_payload_sent":False,
          "firewall_isolation_proven":False
        }
        self.assertEqual(mod.parse_probe(json.dumps(good))["host_reachable_port_bools"][2],True)
        for payload in (
          {**good,"host_reachable_port_bools":["somewhere"]*5},
          {**good,"host_reachable_port_bools":[False]*4},
          {**good,"testnet_reachable_bools":[False]*4},
          {**good,"firewall_isolation_proven":True},
          {**good,"authentication_or_payload_sent":True},
          {**good,"contract":"from-external-source"},
        ):
            with self.assertRaises(mod.Stop):
                mod.parse_probe(json.dumps(payload))
        with self.assertRaises(mod.Stop):
            mod.parse_probe("non-json")

    def test_preflight_fails_closed_on_reserved_names_and_image_mismatch(self):
        dummy_guard = mock.Mock()
        dummy_guard.R23A="homedocker-v140-integrated-canary"
        dummy_guard.R23B="homedocker-v140-r23b-canary"
        orig = {"Config":{"Image":mod.EXPECTED_ORIGINAL_TAG},"Image":IMAGE}
        new = {"Config":{"Image":IMAGE},"Image":IMAGE}
        dummy_guard.container.side_effect=lambda name:orig if name == dummy_guard.R23A else new
        inspect=[{"Id":IMAGE}]
        with mock.patch.object(mod.os,"geteuid",return_value=0), \
             mock.patch.object(mod,"r23d0",return_value=dummy_guard), \
             mock.patch.object(mod,"exists",return_value=False), \
             mock.patch.object(mod,"docker",return_value=json.dumps(inspect)):
            self.assertEqual(mod.preflight(),IMAGE)
            orig["Image"]="sha256:"+"b"*64
            with self.assertRaisesRegex(mod.Stop,"EXPECTED_IMMUTABLE_IMAGE_MISSING"):
                mod.preflight()
        with mock.patch.object(mod.os,"geteuid",return_value=0), \
             mock.patch.object(mod,"r23d0",return_value=dummy_guard), \
             mock.patch.object(mod,"exists",return_value=True):
            with self.assertRaisesRegex(mod.Stop,"PROBE_ALREADY_EXISTS"):
                mod.preflight()

    def test_clean_up_never_prunes_or_removes_production(self):
        commands=[]
        with mock.patch.object(mod,"exists",return_value=True), \
             mock.patch.object(mod,"docker",side_effect=lambda *args,**kwargs:commands.append(args)):
            mod.cleanup(True,True)
        self.assertEqual(commands,[
            ("rm","-f",mod.CONTAINER),
            ("network","rm",mod.NETWORK)
        ])
        self.assertNotIn("prune",str(commands))
        self.assertNotIn("trakt-bridge",str(commands))

    def test_default_cli_is_dry_run_without_creating_resources(self):
        out=io.StringIO()
        with mock.patch("sys.argv",["probe"]), \
             mock.patch.object(mod,"preflight",return_value=IMAGE), \
             mock.patch.object(mod,"apply",side_effect=AssertionError("MUST NOT APPLY")), \
             redirect_stdout(out):
            mod.main()
        self.assertIn("R23D1B_DRY_RUN_PASS",out.getvalue())
        self.assertIn('"network_egress_firewall_verified": false',out.getvalue())

    def test_apply_requires_exact_confirmation(self):
        out=io.StringIO()
        with mock.patch("sys.argv",["probe","--apply"]), \
             mock.patch.object(mod,"preflight",return_value=IMAGE), \
             mock.patch.object(mod,"apply",side_effect=AssertionError("MUST NOT APPLY")), \
             redirect_stderr(out), self.assertRaises(SystemExit) as stop:
            mod.main()
        self.assertEqual(stop.exception.code,1)
        self.assertIn("R23D1B_SAFE_DIAGNOSTIC",out.getvalue())
        self.assertNotIn("sha256:",out.getvalue())

    def test_subprocess_failure_redacts_raw_command_stderr(self):
        payload="AUTH=SECRET/IP=private/path"
        with mock.patch.object(mod.subprocess,"run",return_value=mock.Mock(returncode=2,
                               stdout="",stderr=payload)), self.assertRaises(mod.Stop) as stop:
            mod.docker("image","inspect","broken")
        self.assertEqual(str(stop.exception),"DOCKER_COMMAND_FAILED")
        self.assertNotIn(payload,str(stop.exception))

    def test_all_observations_explicitly_do_not_certify_firewall(self):
        # The final safety decision is a deliberate constant, not a computed
        # PASS inferred from test-net timeouts.
        src=SOURCE.read_text(encoding="utf8")
        self.assertIn('"network_egress_firewall_verified":False',src)
        self.assertIn('"network_egress_firewall_verified":False',src)

if __name__=="__main__":
    import unittest
    unittest.main()
