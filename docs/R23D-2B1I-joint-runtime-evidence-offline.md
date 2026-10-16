# R23D-2B1I — Offline joint NFT/Docker runtime evidence contract

**Checkpoint input:** HomeDocker R23D-2B1H PASS (13/13) and actual **read-only** inventory returned `INVENTORY_PASS_R23D_RESOURCES_ABSENT_BOOT_FENCING_HOLD`. The final R23D container, Docker network, Linux bridge interface and nft guard table are ABSENT. Their absence is the safe observed pre-canary state, not an enforcement proof.

## What is implemented

`scripts/verify-v140-r23d2b1i-nft-docker-evidence-offline.py --fixture-only` instantiates an **entirely synthetic** Docker container inspect, Docker network inspect, and nft JSON table fixture. It invokes the pre-existing, reviewed `attest-v140-r23d-persistent-guard.py.check_persistent_state()` for exact two-chain INPUT/FORWARD nft structural rule matching and one Docker network peer/host-loopback-only image scope. It additionally binds to the original immutable recovered Gateway image SHA and image ID, requires Docker restart policy `no`, and rejects extra DNS, custom PID namespace, additional hosts, and capability additions.

The dedicated tests mutate image ID and source tag, container privilege, Docker restart policy, public bind, extra network peer, unexpected Docker network, ipv6 enablement, read-write proof mount, nft missing/extra/changed rule or chain priority, unexpected DNS/hosts and fixture cardinality. All tests exercise **source-only** Python, not host commands. No `--apply` or host evidence ingestion is supported by this tool. Test CI is discovered from the repository's standard `test/` directory.

## Explicit security limits

- A synthetic fixture with `guard_attested=true` is **not independently trusted host evidence**. This tool returns `SYNTHETIC_STRUCTURE_PASS_LIVE_GUARD_NOT_ATTESTED` and `authenticated_aio_relay_allowed=false` even if every structural check passes.
- No root or host Docker/nft capability is exercised; no kernel nft INPUT/FORWARD hook packet path is tested, and no owned R23D guard is present. No traffic isolation has been demonstrated.
- The current R23D-2A structural parser does not establish provenance, stable snapshot timing, table handle ownership, nft flushing/reboot behavior, IPv6 egress path, or independent supervision. Future runtime acquisition must separately prove those properties before any synthetic canary can be permitted.
- A read-only Docker/nft inventory listing cannot be turned into a canary launch permission. Independent startup fencing and a live verified kill path remain blockers.
- RC6 production, AIOStreams, nginx, Trakt history, user playback and rollback artifacts remain unchanged.

## Source verification only

Use clean pinned detached checkout and run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s test -p 'test_v140_r23d2b1i_nft_docker_evidence.py' -v
python3 -B scripts/verify-v140-r23d2b1i-nft-docker-evidence-offline.py --fixture-only
```

No sudo, Docker daemon, nft, systemd, or network connection is required. This stage **cannot install, start, or qualify** a real R23D guardian or runtime. PR #95 remains Draft until independent kernel enforcement and isolation evidence are obtained in a safe experimental environment.
