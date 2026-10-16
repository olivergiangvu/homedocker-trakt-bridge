# R23D-2B1Q — systemd admission interlock across Docker restart and nft loss

**Scope:** real transient systemd one-shot unit + two real synthetic Docker containers and bridge, only in a disposable GitHub-hosted Ubuntu runner. **NOT an actual VM reboot; not a deployable HomeDocker systemd service.**

## What this adds over B1P

B1P proved a bounded 3-second nft timeout for dual userspace SIGKILL. It still fails open if the nft policy itself disappears, because tc independently allows the narrowly authorized fake TCP path. This phase tests a third *procedural* barrier: the fake gateway is created **with `--network none` and `--restart no`** and is not attached to its private Docker network until a real `systemd-run --wait --collect` one-shot admission checker validates both kernel fences on the current client veth and exact Docker owner identities. After positive admission, deleting the exact lab nft table shows the known remaining unsafe exposure and verifies it is removed by detaching the fake gateway from the bridge.

This is not proof of enforcement against a privileged caller that bypasses the one-shot unit and uses Docker API to connect the server. A future independent launch controller must deny such bypasses.

## Acceptance (expected; observe CI results before marking PASS)

1. Create an INTERNAL isolated real Docker bridge, fake client, and **detached** fake server. Both have restart=no, read-only rootfs, no host ports, fixed lab labels, and no secrets. An unexpected `docker start` on the detached fake server must not make synthetic TCP available to the client.
2. Attach exact tc ingress allowlist/default-deny on client host veth. With lab nft policy **missing**, a real transient systemd one-shot invocation must exit unsuccessfully without connecting or starting the fake server.
3. Create exact scoped nft bridge table with kernel timeout set default EMPTY. The same transient systemd admission must attest tc + nft, issue a test-only 20-second grant, connect fake server to the private bridge and start it. Verify allowed fake TCP 17420 and denied 17421.
4. Deliberately remove only the lab-owned nft table while fake gateway remains attached. Confirm previously authorized fake TCP is now reachable **without nft protection**; this is a release-blocking counterexample. Then forcibly disconnect the fake server's *exact lab private network* and prove allowed TCP is inaccessible even though the server container remains RUNNING.
5. Stop the detached server, restart the **actual Docker daemon** on its dedicated ephemeral runner. Both fake peers must stay stopped, and the server must remain disconnected. Start only the fake client. Docker recreates a veth without tc and nft is still missing: a second systemd one-shot must deny admission.
6. Install tc on the newly authenticated veth and recreate the scoped default-empty nft policy. Only then can systemd-run admit the server again; verify expected allowed/denied fake TCP.
7. Remove only the owned Docker clients/bridge, named nft bridge table, scratch files and transient self-collecting systemd units; verify exact resource absence.

## Hard production release HOLD

A successful 2B1Q CI job qualifies only the disposable **no-network-by-default + systemd transient admission** pattern under a **Docker daemon restart**. It DOES NOT prove a true power-cycle/kernel reboot, privileged Docker API admission bypass resistance, automatic boot ordering or runtime continuous audit of policy disappearance. It does not protect an already attached gateway from `nft flush ruleset` unless a separate independent controller has detached/disabled its network in time. No source/host app data or authenticated AIO/Trakt streams are involved.

The fake lease is 20s purely to keep the synthetic positive controls stable; it is NOT a suggested production timeout. B1P's 3s fail-closed lease remains separate. Do not copy the test-only bridge names, code or nft/systemd commands to HomeDocker.

**Release:** `V140_STABLE_RELEASE_HOLD`; PR #95 remains DRAFT. The next real qualification must use a disposable VM with independently enforced boot-time network admission against unexpected Docker API starts and a true reboot, plus production-shaped non-internal bridge/NAT and authenticated client tests.
