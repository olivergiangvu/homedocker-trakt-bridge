import importlib.util
import io
import json
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"scripts"/"build-v140-r23d2b1c-gateway-only.py"
spec=importlib.util.spec_from_file_location("r23d2b1c_sealed_gateway_builder",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SHA="1234567890abcdef1234567890abcdef12345678"
TAG="homedocker-trakt-bridge:v140-r23d-"+SHA[:12]
ID="sha256:"+"a"*64

def image():
    return {
      "Id":ID,"Os":"linux","Architecture":"amd64",
      "RepoTags":[TAG],
      "Config":{
        "User":"node",
        "WorkingDir":"/app",
        "Entrypoint":["docker-entrypoint.sh"],
        "Cmd":["node","src/gateway-canary-entry.mjs"],
        "Volumes":None,
        "ExposedPorts":{"7101/tcp":{}},
        "Labels":{
            m.ROLE_LABEL:"gateway-only",
            m.SOURCE_LABEL:SHA
        },
        "Env":[
            "NODE_VERSION=24.4.0",
            "NODE_ENV=production",
            "JELLYFIN_GATEWAY_PORT=7101",
            "GATEWAY_PROOF_FILE=/run/identity-proof.json",
        ],
        "Healthcheck":{
            "Test":["CMD-SHELL",
                "wget -q -O /dev/null http://127.0.0.1:7101/health || exit 1"],
        },
      },
    }

def state():
    planner=mock.Mock()
    planner.preflight.return_value={"image_tag":TAG}
    return {
        "base_arch":"amd64",
        "sha":SHA,"tag":TAG,
        "build_command":["docker","build","--pull=false","--network=none",
            "-f",str(ROOT/"Dockerfile.gateway-canary"),"-t",TAG,str(ROOT)],
        "planner":planner,
    }

class GatewayOnlyBuildTests(unittest.TestCase):
    def test_strict_dockerfile_specific_context_excludes_secrets(self):
        self.assertTrue(m.check_deny_by_default_context())
        self.assertEqual(m.EXPECTED_CONTEXT,["**","!Dockerfile.gateway-canary",
            "!package.json","!src/","!src/**"])
        self.assertNotIn("!.env",m.IGNORE.read_text())
        with mock.patch.object(m.IGNORE.__class__,"read_text",
                 return_value="**\n!Dockerfile.gateway-canary\n!package.json\n"
                              "!src/\n!src/**\n!.env\n"), \
             self.assertRaisesRegex(m.Stop,"ISOLATED_DOCKER_CONTEXT_NOT_VERIFIED"):
            m.check_deny_by_default_context()

    def test_final_image_config_and_full_commit_attestation(self):
        self.assertTrue(m.attest_built_image(image(),tag=TAG,sha=SHA,
                                             base_arch="amd64"))
        for change in (
            lambda i:i["Config"]["Labels"].update({m.SOURCE_LABEL:"b"*40}),
            lambda i:i["Config"]["Labels"].update({m.ROLE_LABEL:"production"}),
            lambda i:i["Config"].update({"User":"root"}),
            lambda i:i["Config"].update({"Cmd":["node","src/index.mjs"]}),
            lambda i:i["Config"].update({"WorkingDir":"/tmp"}),
            lambda i:i["Config"].update({"Entrypoint":["sh"]}),
            lambda i:i["Config"].update({"Volumes":{"/app/data":{}}}),
            lambda i:i["Config"].update({"ExposedPorts":{"7101/tcp":{},
                                                         "7000/tcp":{}}}),
            lambda i:i["Config"]["Env"].append("TRAKT_CLIENT_SECRET=WRONG"),
            lambda i:i["Config"]["Env"].append("NODE_ENV=testing"),
            lambda i:i["Config"]["Env"].append("JELLYFIN_GATEWAY_PORT=80"),
            lambda i:i["Config"]["Env"].append("GATEWAY_PROOF_FILE=/tmp/key"),
            lambda i:i["Config"]["Healthcheck"]["Test"].append("unsafe"),
            lambda i:i.update({"Architecture":"arm64"}),
            lambda i:i.update({"Os":"windows"}),
            lambda i:i.update({"Id":"sha256:invalid"}),
            lambda i:i.update({"RepoTags":["unrelated:latest"]}),
        ):
            obj=image()
            change(obj)
            with self.subTest(change=repr(change)),self.assertRaises(m.Stop):
                m.attest_built_image(obj,tag=TAG,sha=SHA,base_arch="amd64")

    def test_valid_image_inspect_only_from_owned_allowlist(self):
        for ref in (TAG,m.load(m.ACQUIRE,"build_test_acquire").PINNED_BASE):
            result=mock.Mock(returncode=0,stdout=json.dumps([image()]).encode(),
                             stderr=b"")
            with mock.patch.object(m.subprocess,"run",return_value=result) as run:
                self.assertEqual(m.inspected_image(ref)["Id"],ID)
                self.assertEqual(run.call_args.args[0],[
                    "docker","image","inspect",ref])
        for ref in ("node:latest","trakt-bridge:latest",
                    "media_core_net","docker.io/library/node:24-alpine",
                    "homedocker-trakt-bridge:v140-r23d-GARBAGE"):
            with self.assertRaises(m.Stop):
                m.inspected_image(ref)

    def test_preflight_requires_cached_exact_base_and_pinned_build_contract(self):
        acquire=mock.Mock()
        acquire.preflight.return_value=True
        acquire.PINNED_BASE=("node:24-alpine@sha256:"+"a"*64)
        acquire.image_inspect.return_value=True
        ready=mock.Mock()
        planner=mock.Mock()
        planner.source_identity.return_value=SHA
        planner.preflight.return_value={
            "image_tag":TAG,
            "commands":{"docker_build":state()["build_command"]},
        }
        ready.load_planner.return_value=planner
        def importer(path,name):
            return acquire if path==m.ACQUIRE else ready
        with mock.patch.object(m,"load",side_effect=importer), \
             mock.patch.object(m,"inspected_image",return_value={
                 "Architecture":"amd64"}), \
             mock.patch.object(m,"check_deny_by_default_context",
                               return_value=True):
            result=m.preflight()
            self.assertEqual(result["tag"],TAG)
            self.assertEqual(result["base_arch"],"amd64")
            self.assertEqual(result["sha"],SHA)
            self.assertEqual(result["build_command"],state()["build_command"])
            planner.preflight.return_value={
                "image_tag":TAG,
                "commands":{"docker_build":["docker","build","--pull=true"]},
            }
            with self.assertRaisesRegex(m.Stop,"REVIEWED_BUILD_CONTRACT_CHANGED"):
                m.preflight()
            acquire.preflight.return_value=False
            with self.assertRaisesRegex(m.Stop,"PINNED_BASE_NOT_CACHED_HOLD_BUILD"):
                m.preflight()

    def test_success_build_uses_cached_node_and_sealed_config_only(self):
        calls=[]
        inspected=[image(),image()]
        def cmd(argv,**kw):
            calls.append((argv,kw))
            return b""
        data=state()
        with mock.patch.object(m,"command",side_effect=cmd), \
             mock.patch.object(m,"inspected_image",
                               side_effect=lambda ref:inspected.pop(0)):
            result=m.apply(data)
        self.assertEqual(result["status"],
              "PINNED_GATEWAY_IMAGE_BUILT_AND_ATTESTED_NO_RUNTIME")
        self.assertEqual(result["local_docker_image_cache_modified"],True)
        self.assertEqual(len(calls),1)
        argv,opts=calls[0]
        self.assertEqual(argv[:2],["docker","build"])
        self.assertIn("--pull=false",argv)
        self.assertIn("--network=none",argv)
        self.assertIn(m.ROLE_LABEL+"=gateway-only",argv)
        self.assertIn(m.SOURCE_LABEL+"="+SHA,argv)
        self.assertIn("DOCKER_CONFIG",opts["env"])
        self.assertEqual(opts["env"]["DOCKER_BUILDKIT"],"1")
        data["planner"].preflight.assert_called_once_with()
        self.assertFalse(result["gateway_canary_container_created"])
        self.assertFalse(result["final_network_or_nft_firewall_created"])
        self.assertFalse(result["aio_authenticated_relay_allowed"])

    def test_image_change_after_build_and_failed_build_stop_without_prune(self):
        wrong=image()
        other=image()
        other["Id"]="sha256:"+"b"*64
        with mock.patch.object(m,"command",return_value=b""), \
             mock.patch.object(m,"inspected_image",side_effect=[wrong,other]), \
             self.assertRaisesRegex(m.Stop,"GATEWAY_IMAGE_ID_CHANGED_AFTER_POSTFLIGHT"):
            m.apply(state())
        with mock.patch.object(m,"command",
                  side_effect=m.Stop("registry token secret")), \
             self.assertRaisesRegex(m.Stop,"PINNED_GATEWAY_BUILD_STOP_INSPECT_LOCAL_TAG") as error:
            m.apply(state())
        self.assertNotIn("token",str(error.exception))

    def test_default_and_unconfirmed_apply_do_not_build(self):
        for argv,successful in ((["script"],True),(["script","--apply"],False)):
            out=io.StringIO()
            err=io.StringIO()
            with mock.patch("sys.argv",argv), \
                 mock.patch.object(m,"preflight",return_value=state()), \
                 mock.patch.object(m,"apply",side_effect=AssertionError("NO BUILD")), \
                 redirect_stdout(out),redirect_stderr(err):
                if successful:
                    m.main()
                    self.assertIn("R23D2B1C_GATEWAY_BUILD_DRY_RUN_PASS",
                                  out.getvalue())
                    self.assertIn('"gateway_image_built": false',out.getvalue())
                else:
                    with self.assertRaises(SystemExit):
                        m.main()
                    self.assertIn("EXPLICIT_GATEWAY_BUILD_CONFIRMATION_REQUIRED",
                                  err.getvalue())
                self.assertNotIn(TAG,out.getvalue()+err.getvalue())
                self.assertNotIn(SHA,out.getvalue()+err.getvalue())

    def test_main_failure_redacts_raw_error_credentials_and_routes(self):
        out=io.StringIO()
        with mock.patch("sys.argv",["gateway-build"]), \
             mock.patch.object(m,"preflight",
                 side_effect=RuntimeError("SECRET TOKEN 10.1.2.3")), \
             redirect_stderr(out),self.assertRaises(SystemExit):
            m.main()
        self.assertIn("R23D2B1C_SAFE_DIAGNOSTIC",out.getvalue())
        self.assertNotIn("TOKEN",out.getvalue())
        self.assertNotIn("10.1.2.3",out.getvalue())

    def test_source_forbids_deploying_gateway_or_firewall(self):
        code=SRC.read_text()
        for forbidden in (
            '"docker","run"', '"docker","network","create"',
            '"nft","--file"', '"iptables","-I"',
            "docker prune","docker image rm",
            "AuthenticateByName","X-Emby-Token",
            "TRAKT_CLIENT_SECRET","aiohistory"
        ):
            self.assertNotIn(forbidden,code)

if __name__=="__main__":
    unittest.main()
