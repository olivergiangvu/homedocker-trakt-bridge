import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit-v140-r23d-host-preflight.py"
spec = importlib.util.spec_from_file_location("r23d_host_check", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def entry(name, *, running=True, image=None, network=None, ports=None):
    return {
        "Name": "/" + name,
        "State": {"Running": running},
        "Config": {"Image": image},
        "Image": "sha256:" + "a"*64,
        "HostConfig": {
            "ReadonlyRootfs": True,
            "NetworkMode": network,
            "PortBindings": {
                f"{inside}/tcp": [{"HostIp":"127.0.0.1",
                                   "HostPort":str(host)}]
                for host,inside in (ports or [])
            },
        },
        "NetworkSettings": {
            "Networks": {network: {}} if network else {}
        },
        "Mounts": [],
    }

class R23DPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {
            "trakt-bridge": entry("trakt-bridge", image=mod.RC6),
            "aiostreams": entry("aiostreams", image=mod.AIO_IMAGE,
                                ports=[(3240, 3000)]),
            "homedocker-resume-contract-canary": entry("homedocker-resume-contract-canary"),
            mod.R23B: entry(mod.R23B, image="sha256:" + "a"*64,
                            network=mod.R23B_NETWORK,
                            ports=[(17041, 7000),(17141,7101)]),
            mod.R23A: entry(mod.R23A, running=False,
                            image="homedocker-trakt-bridge:v140-r23a-e442ce461209"),
        }
        self.patches = [
            mock.patch.object(mod.os, "geteuid", return_value=0),
            mock.patch.object(mod, "container", side_effect=lambda name:self.fixture[name]),
            mock.patch.object(mod, "network", return_value={
                "Internal": False,
                "Options":{"com.docker.network.bridge.enable_ip_masquerade":"false"},
            }),
            mock.patch.object(mod, "command", return_value="\n".join(self.fixture) + "\n"),
            mock.patch.object(mod, "health", return_value=200),
            mock.patch.object(mod, "available_bind_port", return_value=True),
            mock.patch.object(mod.subprocess, "run", return_value=SimpleNamespace(returncode=1)),
        ]
        for patch in self.patches: patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in reversed(self.patches)])

    def test_preflight_is_read_only_and_explicitly_does_not_claim_firewall(self):
        out = mod.audit()
        self.assertEqual(out["status"],"PREFLIGHT_PASS")
        self.assertEqual(out["production_modifications"],0)
        self.assertFalse(out["network_egress_firewall_verified"])
        self.assertFalse(out["aio_get_or_session_created"])
        self.assertFalse(out["gateway_authenticated_live_normalization_proven"])
        self.assertFalse(out["host_relay_running"])

    def test_redacted_stop_reveals_only_approved_fixed_stage_and_reason(self):
        diag = mod.sanitized_diagnostic(mod.Stop(
            "R23B_image_reference_not_immutable_parent"))
        self.assertEqual(diag["status"], "STOP")
        self.assertEqual(diag["stage"], "CANARY_IMAGE_PROVENANCE")
        self.assertEqual(diag["reason"], "R23B_image_reference_not_immutable_parent")
        self.assertEqual(diag["production_modifications"], 0)
        self.assertFalse(diag["aio_get_or_login_attempted"])
        self.assertEqual(
            diag["next_action"], "DO_NOT_DEPLOY_R23D_CANARY_OR_RELAY")

    def test_never_echoes_raw_runtime_errors_or_potential_secrets(self):
        secret = "username=confidential_token=hidden"
        for failure in (mod.Stop(secret), RuntimeError(secret),
                        OSError(secret), ValueError(secret)):
            diag = mod.sanitized_diagnostic(failure)
            self.assertEqual(diag["reason"], "UNKNOWN_HOST_RUNTIME_FAILURE")
            self.assertEqual(diag["stage"], "UNCLASSIFIED")
            self.assertNotIn(secret, str(diag))
        self.assertTrue(all(
            key and key.replace("_", "").isalnum()
            for key in mod.SAFE_DIAGNOSTIC_STAGES
        ))

    def test_each_known_readonly_stop_has_allowed_stage(self):
        with mock.patch.object(mod, "health", return_value=None):
            try:
                mod.audit()
            except mod.Stop as exc:
                diag = mod.sanitized_diagnostic(exc)
                self.assertEqual(diag["stage"], "CANARY_HEALTH")
                self.assertEqual(diag["reason"], "R23B_localhost_health_unavailable")
            else:
                self.fail("R23D0 must stop on failed local health")

    def test_rejects_media_network_or_unread_only_canary(self):
        self.fixture[mod.R23B]["NetworkSettings"]["Networks"]["media_core_net"] = {}
        with self.assertRaises(mod.Stop):
            mod.audit()
        del self.fixture[mod.R23B]["NetworkSettings"]["Networks"]["media_core_net"]
        self.fixture[mod.R23B]["HostConfig"]["ReadonlyRootfs"] = False
        with self.assertRaises(mod.Stop):
            mod.audit()

    def test_real_r23b_sha256_run_reference_is_valid_only_for_exact_r23a_image(self):
        # Actual deploy-v140-r23b-hostcanary.py runs Docker with Image ID,
        # so .Config.Image is sha256:... whereas stopped R23A uses its tag.
        assert self.fixture[mod.R23B]["Config"]["Image"].startswith("sha256:")
        self.assertEqual(mod.audit()["status"], "PREFLIGHT_PASS")
        self.fixture[mod.R23A]["Image"] = "sha256:" + "b"*64
        with self.assertRaisesRegex(mod.Stop, "R23A_rollback_image_different"):
            mod.audit()
        self.fixture[mod.R23A]["Image"] = "sha256:" + "a"*64
        self.fixture[mod.R23B]["Config"]["Image"] = "unverified-external-tag:latest"
        with self.assertRaisesRegex(mod.Stop, "R23B_image_reference"):
            mod.audit()

    def test_original_rollback_tag_must_match_verified_source(self):
        self.fixture[mod.R23A]["Config"]["Image"] = "unverified"
        with self.assertRaisesRegex(mod.Stop, "R23A_rollback_original"):
            mod.audit()

    def test_rejects_expected_production_aio_or_rollback_mutation(self):
        self.fixture["trakt-bridge"]["Config"]["Image"]="untrusted"
        with self.assertRaises(mod.Stop):
            mod.audit()
        self.fixture["trakt-bridge"]["Config"]["Image"]=mod.RC6
        self.fixture[mod.R23A]["State"]["Running"]=True
        with self.assertRaises(mod.Stop):
            mod.audit()

    def test_refuses_existing_candidate_or_port_collision(self):
        self.fixture[mod.R23D] = entry(mod.R23D)
        with mock.patch.object(mod, "command", return_value="\n".join(self.fixture)+"\n"):
            with self.assertRaises(mod.Stop):
                mod.audit()
        self.fixture.pop(mod.R23D)
        self.fixture["aiostreams"]["HostConfig"]["PortBindings"]["1234/tcp"] = [
            {"HostIp":"127.0.0.1", "HostPort":"17145"}
        ]
        with self.assertRaises(mod.Stop):
            mod.audit()

if __name__ == "__main__":
    unittest.main()
