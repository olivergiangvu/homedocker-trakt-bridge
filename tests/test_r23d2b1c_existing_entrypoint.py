"""Offline regression tests for the R23D-2B1C image contract hotfix."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts/build-v140-r23d2b1c-gateway-only.py"
spec=importlib.util.spec_from_file_location("r23d2b1c_build",SCRIPT)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ImageContract(unittest.TestCase):
    def image(self,entrypoint):
        sha=m.EXISTING_SHA
        return {
            "Id":m.EXISTING_IMAGE_ID,"Os":"linux","Architecture":"amd64",
            "RepoTags":["homedocker-trakt-bridge:v140-r23d-"+sha[:12]],
            "Config":{
                "Labels":{m.ROLE_LABEL:"gateway-only",m.SOURCE_LABEL:sha},
                "User":"node","WorkingDir":"/app",
                "Entrypoint":entrypoint,
                "Cmd":["node","src/gateway-canary-entry.mjs"],
                "ExposedPorts":{"7101/tcp":{}},
                "Env":["NODE_ENV=production","JELLYFIN_GATEWAY_PORT=7101",
                       "GATEWAY_PROOF_FILE=/run/identity-proof.json"],
                "Healthcheck":{"Test":["CMD-SHELL",
                    "wget -q -O /dev/null http://127.0.0.1:7101/health || exit 1"]},
            }
        }

    def attest(self,image):
        return m.attest_built_image(
            image,tag="homedocker-trakt-bridge:v140-r23d-"+m.EXISTING_SHA[:12],
            sha=m.EXISTING_SHA,base_arch="amd64")

    def test_exact_inherited_entrypoint_passes(self):
        self.assertTrue(self.attest(self.image(["docker-entrypoint.sh"])))

    def test_absent_entrypoint_rejected(self):
        with self.assertRaises(m.Stop):
            self.attest(self.image(None))

    def test_alternate_entrypoint_rejected(self):
        with self.assertRaises(m.Stop):
            self.attest(self.image(["/bin/sh","-c"]))

    def test_trakt_env_rejected(self):
        image=self.image(["docker-entrypoint.sh"])
        image["Config"]["Env"].append("TRAKT_CLIENT_SECRET=placeholder")
        with self.assertRaises(m.Stop):
            self.attest(image)

    def test_source_mismatch_rejected(self):
        image=self.image(["docker-entrypoint.sh"])
        image["Config"]["Labels"][m.SOURCE_LABEL]="0"*40
        with self.assertRaises(m.Stop):
            self.attest(image)

if __name__=="__main__":
    unittest.main()
