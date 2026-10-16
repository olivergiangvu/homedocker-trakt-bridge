import importlib.util
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import unittest
from unittest import mock

SRC=Path(__file__).resolve().parents[1]/"scripts"/"diagnose-v140-r23d2b1c-build-stop-readonly.py"
spec=importlib.util.spec_from_file_location("r23d2b1c_readonly_triage",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class BuildStopRecoveryTests(unittest.TestCase):
    def test_fixed_reviewed_source_tag_only(self):
        self.assertEqual(m.ORIGINAL_SHA,
            "3c08d06bf08154283d5b548d371729436f9ea392")
        self.assertEqual(m.ORIGINAL_TAG,
            "homedocker-trakt-bridge:v140-r23d-3c08d06bf081")
        self.assertFalse(hasattr(m,"apply"))
        self.assertFalse(hasattr(m,"cleanup"))

    def test_tag_absent_requires_independent_tag_inventory_not_failed_inspect(self):
        def proc(argv,**kwargs):
            if argv[:3]==["docker","image","ls"]:
                return mock.Mock(returncode=0,stdout=b"unrelated:tag\n",stderr=b"")
            self.fail("Tag absence must not inspect missing image")
        with mock.patch.object(m.subprocess,"run",side_effect=proc) as runs:
            self.assertIsNone(m.inspect_image_exact())
        self.assertEqual(runs.call_count,1)

    def test_present_tag_inspected_only_with_exact_old_sha_name(self):
        def proc(argv,**kwargs):
            if argv[:3]==["docker","image","ls"]:
                return mock.Mock(returncode=0,
                    stdout=(m.ORIGINAL_TAG+"\n").encode(),stderr=b"")
            if argv[:3]==["docker","image","inspect"]:
                self.assertEqual(argv[3],m.ORIGINAL_TAG)
                return mock.Mock(returncode=0,stdout=json.dumps([{
                    "Id":"sha256:"+"a"*64,"Config":{"Labels":{}},
                }]).encode(),stderr=b"")
            self.fail("unexpected Docker action")
        with mock.patch.object(m.subprocess,"run",side_effect=proc):
            self.assertIsInstance(m.inspect_image_exact(),dict)

    def test_built_image_qualifies_only_original_builder_attestation(self):
        builder=mock.Mock()
        builder.attest_built_image.return_value=True
        self.assertEqual(m.classify_image({},builder,"amd64"),"ATTESTATION_PASS")
        self.assertEqual(builder.attest_built_image.call_args.kwargs,{
            "tag":m.ORIGINAL_TAG,"sha":m.ORIGINAL_SHA,"base_arch":"amd64"})
        builder.attest_built_image.side_effect=RuntimeError("private-secret")
        self.assertEqual(m.classify_image({},builder,"amd64"),
                         "IMAGE_ATTESTATION_EXCEPTION_REDACTED")
        builder.attest_built_image.side_effect=ValueError(
            "GATEWAY_HEALTHCHECK_REJECTED")
        self.assertEqual(m.classify_image({},builder,"amd64"),
                         "GATEWAY_HEALTHCHECK_REJECTED")

    def test_would_stop_before_tag_inspection_for_unknown_canary_resources(self):
        with mock.patch.object(m,"command") as cmd, \
             mock.patch.object(m,"json_command") as jc:
            cmd.side_effect=[
                mock.Mock(returncode=0,stdout=
                    (m.EXPECTED_CANARY+"\n").encode(),stderr=b""),
                mock.Mock(returncode=0,stdout=b"none\n",stderr=b""),
            ]
            with self.assertRaisesRegex(m.Stop,"UNEXPECTED_FINAL_CANARY"):
                m.matches_no_ephemeral()
            jc.assert_not_called()

    def test_network_nft_and_bridge_presence_abort_with_no_automatic_cleanup(self):
        for step in ("network","link","guard"):
            with self.subTest(step=step):
                names=mock.Mock(returncode=0,stdout=b"other\n",stderr=b"")
                networks=mock.Mock(returncode=0,stdout=(
                    m.EXPECTED_NETWORK+"\n" if step=="network" else "other\n"
                ).encode(),stderr=b"")
                documents=[
                    [{"ifname":m.EXPECTED_INTERFACE if step=="link"
                      else "not-r23d"}],
                    {"nftables":[{
                        "table":{"family":"inet",
                                 "name":m.GUARD_TABLE if step=="guard"
                                 else "something_else"}}]},
                ]
                with mock.patch.object(m,"command",side_effect=[names,networks]), \
                     mock.patch.object(m,"json_command",side_effect=documents):
                    with self.assertRaises(m.Stop):
                        m.matches_no_ephemeral()

    def test_host_readonly_preflight_classifies_absent_or_present_source(self):
        acq=mock.Mock()
        acq.PINNED_BASE="node:24-alpine@sha256:"+"a"*64
        protected=mock.Mock()
        builder=mock.Mock()
        builder.attest_built_image.return_value=True
        modules={m.ACQUIRE:acq,m.BASELINE:protected,m.BUILD:builder}
        modules[m.PLAN]=mock.Mock()
        def importer(path,name): return modules[path]
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"load",side_effect=importer), \
             mock.patch.object(m,"matches_no_ephemeral",return_value=True), \
             mock.patch.object(m,"json_command",return_value=[{"Architecture":"amd64"}]), \
             mock.patch.object(m,"inspect_image_exact",return_value=None):
            self.assertEqual(m.preflight()["tag"],"ABSENT")
        with mock.patch.object(m.os,"geteuid",return_value=0), \
             mock.patch.object(m,"load",side_effect=importer), \
             mock.patch.object(m,"matches_no_ephemeral",return_value=True), \
             mock.patch.object(m,"json_command",return_value=[{"Architecture":"amd64"}]), \
             mock.patch.object(m,"inspect_image_exact",return_value={"Config":{}}):
            result=m.preflight()
        self.assertEqual(result,{"tag":"PRESENT","attestation":"ATTESTATION_PASS"})
        protected.baseline.assert_called()

    def test_no_write_result_never_exposes_image_id_sha_ip_or_environment(self):
        for tag,attestation in [
            ("ABSENT","NOT_APPLICABLE"),
            ("PRESENT","ATTESTATION_PASS"),
            ("PRESENT","GATEWAY_IMAGE_CONFIG_REJECTED"),
        ]:
            with self.subTest(tag=tag,attestation=attestation):
                out=io.StringIO()
                with mock.patch("sys.argv",["triage"]), \
                     mock.patch.object(m,"preflight",return_value={
                         "tag":tag,"attestation":attestation,
                     }),redirect_stdout(out):
                    m.main()
                s=out.getvalue()
                self.assertIn("R23D2B1C_READONLY_TRIAGE_COMPLETE",s)
                self.assertIn('"docker_image_build_or_prune_performed": false',s)
                self.assertIn('"authenticated_jellyfin_session_allowed": false',s)
                for sensitive in (m.ORIGINAL_SHA,m.ORIGINAL_TAG,
                   "172.31.0.1","sha256:"+"a"*64,"TRAKT_CLIENT_SECRET"):
                    self.assertNotIn(sensitive,s)

    def test_redacted_error_does_not_print_raw_token_or_host_address(self):
        err=io.StringIO()
        with mock.patch("sys.argv",["triage"]), \
             mock.patch.object(m,"preflight",
                 side_effect=RuntimeError("SECRET 10.0.0.1 token")), \
             redirect_stderr(err),self.assertRaises(SystemExit):
            m.main()
        self.assertIn("PROTECTED_BASELINE_OR_UNCLASSIFIED_INSPECTION_STOP",
                      err.getvalue())
        self.assertNotIn("10.0.0.1",err.getvalue())
        self.assertNotIn("SECRET",err.getvalue())

    def test_no_mutating_host_command_or_credentials(self):
        source=SRC.read_text()
        for bad in (
            '"docker","build"', '"docker","run"',
            '"docker","network","create"', '"nft","--file"',
            '"docker","pull"', '"docker","rmi"',
            "subprocess.Popen(", "shell=True",
            "AuthenticateByName","X-Emby-Token","docker prune"
        ):
            self.assertNotIn(bad,source)

if __name__=="__main__":
    unittest.main()
