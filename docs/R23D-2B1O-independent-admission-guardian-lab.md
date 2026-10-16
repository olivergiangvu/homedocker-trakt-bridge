# R23D-2B1O — Real-Docker independent admission guardian and watchdog lab

**Status: VERIFIED EPHEMERAL LAB PASS.** GitHub Actions [run 38869176829](https://github.com/olivergiangvu/homedocker-trakt-bridge/actions/runs/38869176829) reports **four of four jobs completed SUCCESS** (application tests, 2B1K/L/M kernel namespace tests, 2B1N Docker daemon recovery, 2B1O independent processes). All nine 2B1O markers below were observed in the dedicated real-Docker job logs. The observed simultaneous guardian + watchdog SIGKILL exposure remains an explicit **NO-GO** production release finding. This experiment runs on a separate, disposable GitHub-hosted Ubuntu VM and **does not install systemd boot units or touch HomeDocker**.

## Goal and actual scope

R23D-2B1N showed that a `tc` filter attached to an old Docker veth is lost when Docker recreates the veth. R23D-2B1O adds two independent Python OS processes on a **dedicated disposable GitHub runner**: a guardian and a watchdog. Each checks the other's kernel PID/liveness and fresh monotonic heartbeat. Only the guardian may admit the exact fake Docker gateway; it checks private Docker owner labels, network, no host ports, no privileges, `--restart no`, exact fake peer, newly identified veth, and re-arms `tc` before it invokes the gateway's `docker start`. The watchdog independently issues **only an exact-name `docker stop`** after guardian SIGKILL, missing or stale heartbeat. Conversely, guardian stops the fake gateway after watchdog SIGKILL.

Fake runtime: one `--internal` Docker bridge, no published ports, two no-privilege/read-only/no-new-privileges/zero-credentials fake Python containers; `172.31.252.2` peer, `172.31.252.3:17420` permitted, `:17421` forbidden. The same B1N fake TCP helper is used, not a real Jellyfin/AIO/Trakt client. Dedicated runner's Docker daemon can be restarted through systemd without touching any existing container after an empty-runner gate.

## Acceptance / known limitation

- **Test 1:** both separate processes alive, exact fake relay reachable, forbidden fake port blocked.
- **Test 2:** `SIGKILL` guardian process; *independent watchdog* must stop fake gateway within bounded polling time.
- **Test 3:** start new pair; `SIGKILL` watchdog process; guardian must stop fake gateway without watchdog cleanup.
- **Test 4:** restart genuine **dedicated runner** Docker daemon; readmission may happen only after revalidating the new host veth and attaching tc, with allowed/forbidden fake TCP checks.
- **Safety counterexample (RELEASE BLOCKER):** kill BOTH processes simultaneously while legitimate relay runs; `tc` still permits authorized fake traffic. Without a separate kernel-enforced bounded lease or a boot-enforced default deny, both userspace processes dying concurrently cannot guarantee a fail-closed stop. The lab must **report the exposure**, not mask it as a production PASS.
- Cleanup kills owned process PIDs, removes only the two owned Docker fake containers and one private Docker bridge, and verifies deletion. No image prune, volume, credentials, host systemd unit installation, nft changes or production changes.

## Definitive limitation

The test may show two OS processes watching one another and safely re-arming a newly verified veth. **That is not a truly independent persistent boot-time guardian**. Both processes are launched by a CI shell, not independent host startup dependencies. A root-triggered unexpected Docker peer start, concurrent guardian/watchdog death, missing kernel fence and actual VM reboot remain unqualified. `V140_STABLE_RELEASE_HOLD` continues even after this individual lab passes. Do not copy this lab's fake ports, network or watch processes to HomeDocker. PR #95 remains **DRAFT, unmerged**.

### Exact observed evidence markers — CI PASS (including unsafe-dual-loss counterexample)

```
R23D2B1O_STOPPED_REAL_DOCKER_PEERS_BEFORE_ADMISSION_PASS
R23D2B1O_GUARDIAN_INDEPENDENT_WATCHDOG_ADMITTED_FAKE_PEER_PASS
R23D2B1O_SIGKILL_GUARDIAN_INDEPENDENT_WATCHDOG_KILL_PASS
R23D2B1O_SIGKILL_WATCHDOG_GUARDIAN_KILL_PASS
R23D2B1O_REAL_DOCKER_DAEMON_RESTART_ADMISSION_CLOSED_PASS
R23D2B1O_POST_DAEMON_RECOVERY_REARMED_BEFORE_ADMISSION_PASS
R23D2B1O_DUAL_GUARDIAN_WATCHDOG_LOSS_EXPOSURE_RELEASE_BLOCKER_PROVEN
R23D2B1O_EPHEMERAL_MUTUAL_FAILURE_LAB_PASS_STABLE_RELEASE_HOLD
R23D2B1O_OWNED_DOCKER_RESOURCE_CLEANUP_PASS
```

Keep any CI failures as explicit diagnostic evidence. Do not announce the lab as qualified if any of the primary job acceptance markers or cleanup gates fail.
