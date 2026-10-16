# R23D-2B1H — Actual Docker/nft/kernel inventory, read-only only

R23D-2B1G was rechecked on HomeDocker after a signal-handler readiness hotfix: 19/19 subprocess tests PASS and five repeated post-ready SIGTERM tests PASS. This checkpoint adds **real host inventory acquisition** without runtime modifications.

## What this audit really does

`scripts/audit-v140-r23d2b1h-real-evidence-readonly.py --audit-only` calls the already verified R23D-2B1E read-only protected image/baseline audit first. It then runs only four fixed read operations:

- `docker ps -a --format '{{.Names}}'` — reject exact R23D canary name collision, including stopped containers.
- `docker network ls --format '{{.Name}}'` — reject final R23D network name collision.
- `ip -j link show` — reject exact Linux R23D bridge-interface collision.
- `nft -j list tables` — reject any existing table using the reserved R23D guard name, including unexpected nft families. If present, ownership/rules require independent review; never overwrite or delete.

The CLI prints only fixed status/boolean fields, not host IPs, actual firewall rule bodies, table inventories, usernames, tokens, session IDs, or paths. Its underlying inherited protected audit may perform existing localhost health checks and `nft --check` **without installing nft rules**. No shell execution, Docker network create, nft add/delete, systemd change, credential login or history write is introduced.

## Critical interpretation

An **absent** R23D table/network/container/bridge is the expected, pre-canary state. The report therefore returns:

`INVENTORY_PASS_R23D_RESOURCES_ABSENT_BOOT_FENCING_HOLD`

This proves only that the four reserved resources are not visible in the inspected kernel/Docker inventories at observation time and that protected prerequisites still pass. **It is explicitly NOT** evidence of an installed nft guard, inspected nft rule semantics, isolated traffic, running independently supervised container, or permission to start a test peer.

If any R23D resource unexpectedly exists, result is `STOP`. This script never claims ownership or attempts cleanup. A future source-pinned runtime inspector must query `nft -j list table inet homedocker_r23d_guard` and exact Docker container/network `inspect`, use the existing structural attester, verify same-observation identity and independently test credential-free positive/negative packet paths. Runtime readiness remains blocked until boot-time fencing and independent kill/supervision are validated in a controlled environment.

## Host execution

Use the exact reviewed SHA from the Draft PR #95 in a fresh, clean detached checkout and run as root only for read-only nft and protected inventory:

```sh
sudo -v
sudo -n python3 -B scripts/audit-v140-r23d2b1h-real-evidence-readonly.py --audit-only
```

No new systemd service, Docker network, nft policy, nginx/AIO/Trakt configuration or client route is touched. No authentic Jellyfin session. **PR stays Draft.**
