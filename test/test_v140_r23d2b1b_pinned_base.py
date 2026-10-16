import importlib.util
import io
import json
import os
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

SRC=Path(__file__).resolve().parents[1]/"scripts"/"acquire-v140-r23d2b1b-pinned-base.py"
spec=importlib.util.spec_from_file_location("r23d2b1b_pinned_docker_base",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

IMAGE_ID="sha256:"+"a"*64
VALID_INSPECT=[{
    "Id":IMAGE_ID,"Os":"linux","Architecture":"amd64",
    "RepoDigests":["node@"+m.DIGEST],
}]

class ExactBaseAcquisitionTests(unittest.TestCase):
    def test_only_one_known_reviewed_digest_and_explicit_confirm(self):
        self.assertEqual(m.PINNED_BASE,"node:24-alpine@sha256:"+
            "ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1")
        self.assertEqual(m.CONFIRM,"R23D2B1B_PULL_EXACT_PINNED_NODE_BASE_ONLY")
        self.assertEqual(m.DIGEST,m.PINNED_BASE.split("@")[1])
        self.assertNotIn("latest",m.PINNED_BASE)
        self.assertTrue(m.IMAGE_ID.fullmatch(IMAGE_ID))

    def test_readiness_rejects_dockerfile_digest_swap_before_pull(self):
        parent=mock.Mock()
        parent.GATEWAY_IMAGE_FILE.read_text.return_value=(
            "FROM node:24-alpine@sha256:"+"b"*64+"\n")
        parent.check_gateway_source.return_value=True
        readiness=mock.Mock()
        readiness.load_planner.return_value=parent
        readiness.BASE_RE=__import__("re").compile(
            r"(?m)^FROM\s+(node:24-alpine@sha256:[a-f0-9]{64})\s*$")
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"require_local_docker_socket",return_value=True), \
             self.assertRaisesRegex(m.Stop,"SOURCE_IMAGE_DIGEST_CHANGED"):
            m.preflight(readiness)
        readiness.preflight.assert_not_called()
        with mock.patch.object(m.os,"geteuid",return_value=1000), \
             self.assertRaisesRegex(m.Stop,"ROOT_REQUIRED"):
            m.preflight(readiness)

    def test_true_source_identity_matches_base_then_protected_preflight(self):
        parent=mock.Mock()
        parent.check_gateway_source.return_value=True
        parent.GATEWAY_IMAGE_FILE.read_text.return_value="FROM "+m.PINNED_BASE+"\n"
        readiness=mock.Mock()
        readiness.load_planner.return_value=parent
        readiness.BASE_RE=__import__("re").compile(
            r"(?m)^FROM\s+(node:24-alpine@sha256:[a-f0-9]{64})\s*$")
        readiness.preflight.return_value=False
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"require_local_docker_socket",return_value=True):
            self.assertFalse(m.preflight(readiness))
        readiness.preflight.assert_called_once_with()

    def test_only_local_rootful_default_docker_context_is_allowed(self):
        with mock.patch.dict(m.os.environ,{"DOCKER_HOST":"tcp://10.0.0.5:2375"},
                             clear=False), \
             self.assertRaisesRegex(m.Stop,"REMOTE_DOCKER_HOST_DISALLOWED"):
            m.require_local_docker_socket()
        with mock.patch.dict(m.os.environ,{"DOCKER_CONTEXT":"remote"},
                             clear=False), \
             self.assertRaisesRegex(m.Stop,"REMOTE_DOCKER_HOST_DISALLOWED"):
            m.require_local_docker_socket()
        env_patch={"DOCKER_HOST":"","DOCKER_CONTEXT":""}
        with mock.patch.dict(m.os.environ,env_patch,clear=False), \
             mock.patch.object(m.Path,"is_socket",return_value=False), \
             self.assertRaisesRegex(m.Stop,"LOCAL_DOCKER_SOCKET_UNAVAILABLE"):
            m.require_local_docker_socket()
        with mock.patch.dict(m.os.environ,env_patch,clear=False), \
             mock.patch.object(m.Path,"is_socket",return_value=True), \
             mock.patch.object(m.subprocess,"run",return_value=mock.Mock(
                 returncode=0,stdout=b"remote\n")), \
             self.assertRaisesRegex(m.Stop,"REMOTE_DOCKER_HOST_DISALLOWED"):
            m.require_local_docker_socket()
        with mock.patch.dict(m.os.environ,env_patch,clear=False), \
             mock.patch.object(m.Path,"is_socket",return_value=True), \
             mock.patch.object(m.subprocess,"run",return_value=mock.Mock(
                 returncode=0,stdout=b"default\n")) as runner:
            self.assertTrue(m.require_local_docker_socket())
            self.assertEqual(runner.call_args.args[0],["docker","context","show"])

    def test_image_provenance_requires_exact_digest_and_linux_os(self):
        for value in ("node@"+m.DIGEST,
                      "docker.io/library/node@"+m.DIGEST,
                      "library/node@"+m.DIGEST):
            payload=[{**VALID_INSPECT[0],"RepoDigests":[value]}]
            p=mock.Mock(returncode=0,stdout=json.dumps(payload).encode(),stderr=b"")
            with mock.patch.object(m.subprocess,"run",return_value=p) as run:
                self.assertTrue(m.image_inspect(m.PINNED_BASE))
            self.assertEqual(run.call_args.args[0],
                ["docker","image","inspect",m.PINNED_BASE])
        for mutated in (
            {"RepoDigests":["node@sha256:"+"0"*64]},
            {"RepoDigests":[]},
            {"RepoDigests":["evil-registry.example/node@"+m.DIGEST]},
            {"Os":"windows"},
            {"Id":"sha256:bad"},
        ):
            payload=[{**VALID_INSPECT[0],**mutated}]
            p=mock.Mock(returncode=0,stdout=json.dumps(payload).encode())
            with mock.patch.object(m.subprocess,"run",return_value=p), \
                 self.assertRaises(m.Stop):
                m.image_inspect(m.PINNED_BASE)
        with self.assertRaisesRegex(m.Stop,"DISALLOWED_DOCKER_IMAGE_REFERENCE"):
            m.image_inspect("node:latest")

    def test_default_dry_run_never_calls_pull_or_changes_cache(self):
        result=io.StringIO()
        with mock.patch("sys.argv",["script"]), \
             mock.patch.object(m,"load_readiness",return_value=object()), \
             mock.patch.object(m,"preflight",return_value=False), \
             mock.patch.object(m,"apply",side_effect=AssertionError("PULL_NOT_AUTHORIZED")), \
             redirect_stdout(result):
            m.main()
        self.assertIn("R23D2B1B_PINNED_ACQUISITION_DRY_RUN_PASS",result.getvalue())
        self.assertIn("READY_FOR_EXPLICIT_DIGEST_ONLY_PULL",result.getvalue())
        self.assertIn('"docker_image_cache_modified": false',result.getvalue())

    def test_apply_needs_exact_confirmation_even_if_cached(self):
        stderr=io.StringIO()
        with mock.patch("sys.argv",["script","--apply"]), \
             mock.patch.object(m,"load_readiness",return_value=object()), \
             mock.patch.object(m,"preflight",return_value=False), \
             mock.patch.object(m,"apply",side_effect=AssertionError("PULL_NOT_AUTHORIZED")), \
             redirect_stderr(stderr),self.assertRaises(SystemExit) as e:
            m.main()
        self.assertEqual(e.exception.code,1)
        self.assertIn("EXPLICIT_IMAGE_PULL_CONFIRMATION_REQUIRED",stderr.getvalue())

    def test_only_explicit_pin_can_pull_no_registry_credentials_used(self):
        r=mock.Mock(returncode=0,stdout=b"silent",stderr=b"")
        readiness=mock.Mock()
        readiness.preflight.return_value=True
        with mock.patch.object(m.subprocess,"run",return_value=r) as runner, \
             mock.patch.object(m,"image_inspect",return_value=True) as inspect:
            result=m.apply(readiness)
        self.assertEqual(result["status"],"PINNED_BASE_CACHED_AND_PROTECTED_POSTFLIGHT_PASS")
        self.assertEqual(runner.call_count,1)
        args,kwargs=runner.call_args
        self.assertEqual(args[0],["docker","pull","--quiet",m.PINNED_BASE])
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs["check"])
        self.assertIn("DOCKER_CONFIG",kwargs["env"])
        self.assertNotEqual(kwargs["env"]["DOCKER_CONFIG"],os.environ.get("HOME",""))
        self.assertFalse(result["aio_jellyfin_session_allowed"])
        self.assertFalse(result["gateway_image_built"])
        self.assertFalse(result["canary_network_or_nft_firewall_created"])
        inspect.assert_called_once_with(m.PINNED_BASE)
        readiness.preflight.assert_called_once_with()

    def test_failed_pull_does_not_delete_or_emit_raw_registry_error(self):
        p=mock.Mock(returncode=1,stdout=b"",stderr=b"DOCKER SECRET password")
        with mock.patch.object(m.subprocess,"run",return_value=p), \
             self.assertRaisesRegex(m.Stop,"EXACT_DIGEST_PULL_FAILED"):
            m.apply(mock.Mock())
        err=io.StringIO()
        with mock.patch("sys.argv",["script","--apply",
                "--confirm",m.CONFIRM]), \
             mock.patch.object(m,"load_readiness",return_value=object()), \
             mock.patch.object(m,"preflight",return_value=False), \
             mock.patch.object(m,"apply",
                  side_effect=RuntimeError("registry secret user-identity")), \
             redirect_stderr(err),self.assertRaises(SystemExit):
            m.main()
        self.assertIn("R23D2B1B_SAFE_DIAGNOSTIC",err.getvalue())
        self.assertNotIn("secret",err.getvalue())
        self.assertIn('"docker_image_cache_may_have_changed": true',err.getvalue())

    def test_missing_image_after_pull_or_failed_postflight_is_stop(self):
        p=mock.Mock(returncode=0,stdout=b"",stderr=b"")
        with mock.patch.object(m.subprocess,"run",return_value=p), \
             mock.patch.object(m,"image_inspect",
                   side_effect=m.Stop("POST_PULL_DIGEST_OR_PLATFORM_NOT_VERIFIED")), \
             self.assertRaisesRegex(m.Stop,"POST_PULL_DIGEST"):
            m.apply(mock.Mock())
        readiness=mock.Mock()
        readiness.preflight.return_value=False
        with mock.patch.object(m.subprocess,"run",return_value=p), \
             mock.patch.object(m,"image_inspect",return_value=True), \
             self.assertRaisesRegex(m.Stop,"POST_PULL_PRODUCTION_OR_BASE"):
            m.apply(readiness)

    def test_cached_base_apply_skips_network_pull_but_still_attests(self):
        stdout=io.StringIO()
        with mock.patch("sys.argv",["script","--apply",
             "--confirm",m.CONFIRM]), \
             mock.patch.object(m,"load_readiness",return_value=object()), \
             mock.patch.object(m,"preflight",return_value=True), \
             mock.patch.object(m,"image_inspect",return_value=True) as inspect, \
             mock.patch.object(m,"apply",side_effect=AssertionError("PULL_NOT_AUTHORIZED")), \
             redirect_stdout(stdout):
            m.main()
        self.assertIn("ALREADY_CACHED_VERIFIED_NO_PULL",stdout.getvalue())
        inspect.assert_called_once_with(m.PINNED_BASE)

    def test_source_has_no_host_firewall_container_or_client_action(self):
        src=SRC.read_text()
        for prohibited in (
            '"docker","build"', '"docker","run"',
            '"docker","network","create"', '"nft","--file"',
            '"iptables","-I"', "docker prune", "docker image rm",
            "AuthenticateByName", "X-Emby-Token",
            "TRAKT_CLIENT_SECRET", "AIO_HISTORY"):
            with self.subTest(prohibited=prohibited):
                self.assertNotIn(prohibited,src)

if __name__=="__main__":
    unittest.main()
