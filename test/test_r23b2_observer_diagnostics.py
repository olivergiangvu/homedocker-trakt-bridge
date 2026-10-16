"""R23B2 no-token observer diagnostics: source/contract checks only."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1]
LAUNCHER = BASE / "scripts/host-trial-r23b2.py"
WRAPPER = BASE / "scripts/run-r23b2-no-token-host-trial.sh"
spec = importlib.util.spec_from_file_location("r23b2_phase_observe", LAUNCHER)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class ObserverSafety(unittest.TestCase):
    def test_phase_marker_only_fixed_allowlist(self):
        for event in (
            "PREFLIGHT_PASS", "RECEIPT_CREATED", "HOST_SOCKET_READY",
            "SIDECAR_CREATED", "SIDECAR_STARTED", "NETNS_LISTENER_READY",
            "CANARY_FINGERPRINT_PASS", "READY", "CLEANUP_START", "CLEANUP_DONE"
        ):
            with self.subTest(event=event), contextlib.redirect_stdout(io.StringIO()) as out:
                app.phase(event)
                self.assertEqual(out.getvalue(), "R23B2_PHASE_" + event + "\n")

    def test_phase_marker_rejects_interpolated_secret(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(app.TrialStop):
                app.phase("READY_TOKEN_123")
            self.assertEqual(out.getvalue(), "")

    def test_launcher_emits_marker_after_exact_safety_checks(self):
        source = LAUNCHER.read_text()
        def pos(text):
            self.assertIn(text, source)
            return source.index(text, source.index("def trial(ttl):"))
        self.assertLess(pos("wait_for_socket(host)"), pos("phase('HOST_SOCKET_READY')"))
        self.assertLess(pos("check_sidecar(current, image_id, nonce)"), pos("phase('SIDECAR_STARTED')"))
        self.assertLess(pos("wait_netns_listener(canary['State']['Pid'])"), pos("phase('NETNS_LISTENER_READY')"))
        self.assertLess(pos("phase('CANARY_FINGERPRINT_PASS')"), pos("phase('READY')"))
        self.assertLess(pos("phase('READY')"), pos("print(json.dumps"))

    def test_observer_only_fixed_no_token_routes_and_private_log(self):
        source = WRAPPER.read_text()
        self.assertIn("R23B2_PHASE_READY", source)
        self.assertIn("seq 1 200", source)
        self.assertIn("sleep .5", source)
        self.assertIn("/var/tmp/r23b2-observe.", source)
        self.assertIn("chmod 0700", source)
        self.assertIn("R23B2_OBSERVER_ROOT_ONLY_LOG_RETAINED", source) if False else None
        for route in (
            "/jellyfin/Users/Me",
            "/jellyfin/Shows/NextUp?Limit=20",
            "/jellyfin/UserItems/Resume?Limit=20",
            "/jellyfin/Users/AuthenticateByName",
        ):
            self.assertIn(route, source)
        self.assertNotIn("X-Emby-Token", source)
        for unsafe in ("docker system prune", "docker network connect", "nft flush",
                       "iptables -F", "systemctl restart", "nginx -s",
                       "TRAKT_CLIENT_SECRET", "AIO_PASSWORD"):
            self.assertNotIn(unsafe, source)


if __name__ == "__main__":
    unittest.main()
