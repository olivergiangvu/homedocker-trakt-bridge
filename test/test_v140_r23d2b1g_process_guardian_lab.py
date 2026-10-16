"""R23D-2B1G: real subprocess integration tests for synthetic-only guardian."""
import ast
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"guardian-v140-r23d2b1g-process-lab.py"
spec=importlib.util.spec_from_file_location("r23d2b1g_lab",SCRIPT)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
EPOCH="R23D_SYNTHETIC_LAB_0001"

def proof(n=1,**changes):
    p={"epoch":EPOCH,"seq":n,"guard_attested":True,
       "docker_identity_attested":True,"fence_ready":True,
       "synthetic_only":True,"reboot_detected":False}
    p.update(changes)
    return p

def invoke(*records,extra_args=()):
    text=b"".join((json.dumps(rec).encode()+b"\n") if not isinstance(rec,bytes)
                  else rec+b"\n" for rec in records)
    p=subprocess.run([sys.executable,"-B",str(SCRIPT),"--lab-only",*extra_args],
                     input=text,capture_output=True,timeout=4)
    output=[json.loads(line) for line in p.stdout.splitlines()]
    return p,output

class ProcessLab(unittest.TestCase):
    def test_real_child_process_pass_two_frames(self):
        p,rows=invoke(proof(1),proof(2))
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual([r["status"] for r in rows],[
            "LAB_EVIDENCE_ACCEPTED_NOT_HOST_PROOF",
            "LAB_EVIDENCE_ACCEPTED_NOT_HOST_PROOF",
            "LAB_EXERCISE_PASS_NO_RUNTIME_AUTHORIZATION"])
        self.assertTrue(all(not row["real_canary_start_authorized"] for row in rows))
        self.assertTrue(all(not row["authenticated_aio_relay_allowed"] for row in rows))
    def test_replay_seq_latches(self):
        p,rows=invoke(proof(1),proof(1))
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"EPOCH_OR_SEQUENCE_REPLAY")
    def test_wrong_epoch_latches(self):
        p,rows=invoke(proof(1,epoch="R23D_SYNTHETIC_EVIL_0001"))
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"EPOCH_OR_SEQUENCE_REPLAY")
    def test_guard_false_latches(self):
        p,rows=invoke(proof(1,guard_attested=False))
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"SYNTHETIC_GUARD_OR_FENCE_NOT_ATTESTED")
    def test_fence_false_latches(self):
        p,rows=invoke(proof(1,fence_ready=False))
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["status"],"LAB_LATCHED_STOP")
    def test_non_synthetic_latches(self):
        p,rows=invoke(proof(1,synthetic_only=False))
        self.assertEqual(p.returncode,2)
        self.assertFalse(rows[-1]["authenticated_aio_relay_allowed"])
    def test_reboot_latches(self):
        p,rows=invoke(proof(1,reboot_detected=True))
        self.assertEqual(rows[-1]["reason"],"REBOOT_FAIL_CLOSED")
        self.assertEqual(p.returncode,2)
    def test_eof_after_first_frame_latches(self):
        p,rows=invoke(proof(1))
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"EVIDENCE_PIPE_EOF")
    def test_unknown_key_latches_without_reprinting_it(self):
        p,rows=invoke(proof(1,SECRET_HOST_TOKEN="do-not-echo"))
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"EVIDENCE_SHAPE_REJECTED")
        self.assertNotIn(b"do-not-echo",p.stdout+p.stderr)
    def test_duplicate_json_key_rejected(self):
        raw=b'{"epoch":"R23D_SYNTHETIC_LAB_0001","epoch":"R23D_SYNTHETIC_LAB_0001"}'
        p,rows=invoke(raw)
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"DUPLICATE_JSON_KEY")
    def test_malformed_json_latches(self):
        p,rows=invoke(b'{ "oops"')
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"SYNTHETIC_JSON_REJECTED")
    def test_extra_frame_in_same_read_latches(self):
        p,rows=invoke(proof(1),proof(2),proof(3))
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"EXTRA_EVIDENCE_AFTER_LAB_BOUND")
    def test_heartbeat_timeout_is_monotonic_process_deadline(self):
        p=subprocess.Popen([sys.executable,"-B",str(SCRIPT),
                           "--lab-only","--timeout-ms","100"],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        try:
            code=p.wait(timeout=3)
            rows=[json.loads(line) for line in p.stdout.read().splitlines()]
            self.assertEqual(code,2)
            self.assertEqual(rows[-1]["reason"],"HEARTBEAT_TIMED_OUT")
        finally:
            if p.poll() is None:p.kill()
            p.communicate(timeout=2)
    def test_term_signal_latches(self):
        p=subprocess.Popen([sys.executable,"-B",str(SCRIPT),
                            "--lab-only","--timeout-ms","2500"],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        try:
            time.sleep(0.15)
            p.send_signal(signal.SIGTERM)
            code=p.wait(timeout=3)
            rows=[json.loads(line) for line in p.stdout.read().splitlines()]
            self.assertEqual(code,2)
            self.assertEqual(rows[-1]["reason"],"PROCESS_INTERRUPTED_FAIL_CLOSED")
        finally:
            if p.poll() is None:p.kill()
            p.communicate(timeout=2)
    def test_no_lab_flag_never_starts_guardian(self):
        p=subprocess.run([sys.executable,"-B",str(SCRIPT)],capture_output=True,timeout=3)
        self.assertNotEqual(p.returncode,0)
        self.assertFalse(p.stdout)
    def test_bound_max_events_rejects_unbounded_run(self):
        p=subprocess.run([sys.executable,"-B",str(SCRIPT),
                          "--lab-only","--max-events","1000"],
                         input=b"",capture_output=True,timeout=3)
        self.assertEqual(p.returncode,2)
        self.assertEqual(json.loads(p.stdout)["reason"],"LAB_BOUNDS_INVALID")
    def test_raw_json_frame_length_rejected(self):
        p,rows=invoke(b"A"*1100)
        self.assertEqual(p.returncode,2)
        self.assertEqual(rows[-1]["reason"],"FRAME_TOO_LARGE_OR_EMPTY")
    def test_script_has_no_host_mutation_imports(self):
        tree=ast.parse(SCRIPT.read_text(encoding="utf8"))
        imports=set()
        for n in ast.walk(tree):
            if isinstance(n,ast.Import):
                imports.update(x.name.split(".")[0] for x in n.names)
            if isinstance(n,ast.ImportFrom) and n.module:
                imports.add(n.module.split(".")[0])
        self.assertFalse(imports & {"subprocess","docker","requests","urllib",
                                     "ctypes","shutil"})
        txt=SCRIPT.read_text(encoding="utf8")
        self.assertNotIn("Popen(",txt)
        self.assertNotIn("systemctl start",txt)
        self.assertNotIn("nft add",txt)

if __name__=="__main__":
    unittest.main()
