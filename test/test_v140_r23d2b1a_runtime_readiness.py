import importlib.util
import io
import json
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"scripts"/"audit-v140-r23d2b1-offline-runtime-readiness.py"
spec=importlib.util.spec_from_file_location("r23d2b1_runtime_ready",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
BASE=("node:24-alpine@sha256:"
      "ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1")
TAG="homedocker-trakt-bridge:v140-r23d-66ad5aae342c"

class ReadinessTests(unittest.TestCase):
    def test_exact_base_digest_matches_gateway_only_source(self):
        p=ROOT/"Dockerfile.gateway-canary"
        self.assertEqual(m.BASE_RE.findall(p.read_text()),[BASE])
        self.assertTrue(m.validate_lifecycle(m.STAGES))
        with self.assertRaisesRegex(m.Stop,"LIFECYCLE_SEQUENCE_INVALID"):
            m.validate_lifecycle(m.STAGES[::-1])
        with self.assertRaisesRegex(m.Stop,"LIFECYCLE_SEQUENCE_INVALID"):
            m.validate_lifecycle(list(m.STAGES))

    def test_rollback_guard_never_removed_until_canary_and_network_absent(self):
        self.assertEqual(m.allowed_cleanup(canary_absent=False,
            network_absent=True,guard_unchanged=True),
            "RETAIN_GUARD_AND_NETWORK_INSPECT_CANARY")
        self.assertEqual(m.allowed_cleanup(canary_absent=True,
            network_absent=False,guard_unchanged=True),
            "RETAIN_GUARD_REMOVE_ONLY_ATTESTED_EMPTY_NETWORK")
        self.assertEqual(m.allowed_cleanup(canary_absent=True,
            network_absent=True,guard_unchanged=False),
            "RETAIN_GUARD_MANUAL_OWNERSHIP_REVIEW")
        self.assertEqual(m.allowed_cleanup(canary_absent=True,
            network_absent=True,guard_unchanged=True),
            "REMOVE_ONLY_UNCHANGED_OWNED_GUARD_LAST")
        for val in (None,0,1,"true"):
            with self.assertRaisesRegex(m.Stop,"ROLLBACK_EVIDENCE_MUST_BE_BOOLEAN"):
                m.allowed_cleanup(canary_absent=val,
                    network_absent=True,guard_unchanged=True)

    def test_image_cache_only_queries_docker_inspect_and_daemon_health(self):
        calls=[]
        id_="sha256:"+"a"*64
        def proc(argv,**kwargs):
            calls.append(argv)
            if argv[1:3]==["image","inspect"]:
                return mock.Mock(returncode=0,stdout=json.dumps([{
                    "Id":id_,"RepoTags":[TAG]}]).encode(),stderr=b"")
            if argv[1]=="info":
                return mock.Mock(returncode=0,stdout=b"29.8.0",stderr=b"")
            self.fail("unexpected Docker command")
        with mock.patch.object(m.subprocess,"run",side_effect=proc):
            self.assertEqual(m.inspect_image(BASE),id_)
            self.assertEqual(m.inspect_image(TAG),id_)
        self.assertEqual([c[1:3] for c in calls],
                         [["image","inspect"],["info","--format"],
                          ["image","inspect"],["info","--format"]])
        for bad in ("node:latest","sha256:"+"a"*64,
                    "ghcr.io/viren070/aiostreams:latest",
                    "node:24-alpine@sha256:"+"G"*64):
            with self.assertRaisesRegex(m.Stop,"IMAGE_REFERENCE_UNEXPECTED"):
                m.inspect_image(bad)

    def test_missing_pinned_image_stays_pending_and_does_not_pull(self):
        commands=[]
        def proc(argv,**kwargs):
            commands.append(argv)
            if argv[1]=="info":
                return mock.Mock(returncode=0,stdout=b"29.8.0",stderr=b"")
            return mock.Mock(returncode=1,stdout=b"",stderr=b"private local path")
        with mock.patch.object(m.subprocess,"run",side_effect=proc):
            self.assertIsNone(m.inspect_image(BASE))
        self.assertEqual(commands[0][:3],["docker","image","inspect"])
        self.assertEqual(commands[1][:2],["docker","info"])
        self.assertNotIn("pull",str(commands))

    def test_preflight_requires_no_collision_cached_base_optional(self):
        planner=mock.Mock()
        planner.preflight.return_value={"image_tag":TAG}
        planner.GATEWAY_IMAGE_FILE=ROOT/"Dockerfile.gateway-canary"
        for locally_cached in (True,False):
            with mock.patch.object(m,"load_planner",return_value=planner), \
                 mock.patch.object(m,"inspect_image",side_effect=[
                     "sha256:"+"a"*64 if locally_cached else None,None
                 ]) as inspect, \
                 mock.patch.object(m,"check_ports",return_value=True):
                self.assertEqual(m.preflight(),locally_cached)
                self.assertEqual([c.args[0] for c in inspect.call_args_list],
                                 [BASE,TAG])
        with mock.patch.object(m,"load_planner",return_value=planner), \
             mock.patch.object(m,"inspect_image",side_effect=[
                "sha256:"+"a"*64,"sha256:"+"b"*64
             ]),mock.patch.object(m,"check_ports",return_value=True), \
             self.assertRaisesRegex(m.Stop,"PINNED_FINAL_IMAGE_TAG_ALREADY_PRESENT"):
            m.preflight()

    def test_port_occupancy_fail_closed(self):
        class Busy:
            def __init__(self,*args): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def bind(self,address):
                raise OSError("secret path/hostname and occupied port")
        with mock.patch.object(m.socket,"socket",side_effect=Busy), \
             self.assertRaisesRegex(m.Stop,"SYNTHETIC_PORT_ALREADY_IN_USE") as caught:
            m.check_ports()
        self.assertNotIn("secret",str(caught.exception))

    def test_main_redacts_subnet_digest_and_status_no_session(self):
        for has_base in (True,False):
            out=io.StringIO()
            with mock.patch.object(m,"preflight",return_value=has_base), \
                 redirect_stdout(out):
                m.main()
            result=out.getvalue()
            self.assertIn("R23D2B1A_READONLY_RUNTIME_READINESS_COMPLETE",result)
            self.assertIn('"real_aio_jellyfin_session_allowed": false',result)
            self.assertIn('"final_network_container_or_firewall_modified": false',result)
            for sensitive in (BASE,TAG,"10.231.240.", "br-r23dgate",
                              "identity-proof.json","table inet","X-Emby-Token"):
                self.assertNotIn(sensitive,result)
            self.assertIn("READY_TO_DESIGN_SUPERVISED_TRIAL" if has_base
                 else "PINNED_BASE_IMAGE_NOT_CACHED_HOLD_BUILD",result)

    def test_main_stop_never_prints_sensitive_error_details(self):
        out=io.StringIO()
        with mock.patch.object(m,"preflight",
                 side_effect=RuntimeError("X-Emby-Token=PRIVATE 172.31.14.2")), \
             redirect_stderr(out),self.assertRaises(SystemExit) as caught:
            m.main()
        self.assertEqual(caught.exception.code,1)
        self.assertIn("R23D2B1A_SAFE_DIAGNOSTIC",out.getvalue())
        self.assertNotIn("PRIVATE",out.getvalue())

    def test_no_hidden_apply_build_or_firewall_mutation_entry(self):
        code=SRC.read_text()
        for unsafe in (
             '"--apply"', '"--confirm"', '"network","create"',
             '"build"', '"pull"', '"run","--detach"',
             '"nft","delete"', '"nft","--file","-"',
             "subprocess.Popen(", "shell=True",
             "AuthenticateByName", "requests.post(",
             "JELLYFIN_GATEWAY_AIO_USER_ID="):
            self.assertNotIn(unsafe,code)

if __name__=="__main__":
    unittest.main()
