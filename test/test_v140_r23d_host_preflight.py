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
            mod.R23B: entry(mod.R23B, network=mod.R23B_NETWORK,
                            ports=[(17041, 7000),(17141,7101)]),
            mod.R23A: entry(mod.R23A, running=False),
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

    def test_rejects_media_network_or_unread_only_canary(self):
        self.fixture[mod.R23B]["NetworkSettings"]["Networks"]["media_core_net"] = {}
        with self.assertRaises(mod.Stop):
            mod.audit()
        del self.fixture[mod.R23B]["NetworkSettings"]["Networks"]["media_core_net"]
        self.fixture[mod.R23B]["HostConfig"]["ReadonlyRootfs"] = False
        with self.assertRaises(mod.Stop):
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
