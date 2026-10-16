# R23D-2B1J — Real read-only Docker/nft triad collector (HOLD-only)

**Prerequisite evidence:** HomeDocker R23D-2B1I 18/18 offline tests PASS; the previous R23D-2B1H host inventory reported R23D Docker network/container/bridge/nft guard absent and protected RC6/R23B/R23A baseline intact.

## Change

`scripts/collect-v140-r23d2b1j-docker-nft-readonly.py --audit-only` performs bounded fixed-command READS on the real HomeDocker Linux/Docker/nft stack. The script cannot create any resource or authorize a session. It prints only fixed categorical booleans and status, never raw Docker inspect JSON (which can contain sensitive env), network addresses, nft rule bodies, tokens or credentials.

Reads only: `docker ps -a --format` all names, `docker network ls --format` all names, `ip -j link show`, `nft -j list tables`. If **all four reserved** R23D identities are absent, it re-runs the entire R23D-2B1H protected baseline/image/absence audit and returns `ABSENT_CONFIRMED_BOOT_FENCING_HOLD`.

If **some** but not all four identities exist, or the reserved nft name appears under an unexpected table family, it FAILS CLOSED without any delete/stop/repair.

If **all four** identities exist, the collector obtains the exact-name triad with `docker container inspect`, `docker network inspect`, and `nft -j list table inet homedocker_r23d_guard`. In memory, it reuses the existing reviewed R23D-2A structural verifier to compare the exact original image ID/source tag, Docker restart=no, single peer, network scope and nft INPUT/FORWARD expressions. Even a structural match reports only `STRUCTURE_OBSERVED_UNTRUSTED_BOOT_FENCING_HOLD`, not authorization. Presence may be unowned/colliding or compromised. It does NOT inherit the absent-resource protected baseline as evidence of an active protected runtime.

## Limits and gating

- The reads occur at distinct instants and are subject to TOCTOU races; no atomic cross-subsystem snapshot.
- Local root Docker socket/nft access does not establish **independently authenticated** evidence or ownership of R23D resources.
- Valid nft JSON matches **structural semantics**, NOT effective real INPUT/FORWARD packet isolation, IPv6 bypass, NAT/conntrack interactions, nft rules that disappeared during inspection, reboot persistence, or a live kill/fence mechanism.
- No systemd guardian, separate independent boot-time fence, real synthetic TCP probes, or authenticated AIO/Jellyfin session is created.
- Unexpected resources require manual evidence review; they must not be removed or repurposed.
- Maintains Draft PR #95 and no production/Trakt/nginx/RC6/R23B/R23A modifications.

## Operator (SHA-pinned clean detached checkout only)

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s test -p 'test_v140_r23d2b1j_readonly_collector.py' -v
sudo -v
sudo -n python3 -B scripts/collect-v140-r23d2b1j-docker-nft-readonly.py --audit-only
```

Expected on the previously observed clean host: `ABSENT_CONFIRMED_BOOT_FENCING_HOLD`. This is safe checkpoint completion for read-only evidence collection, **not** a pass for runtime canary. A future approved isolated **boot-time fence + external watchdog** is required before any runtime network mutation.
