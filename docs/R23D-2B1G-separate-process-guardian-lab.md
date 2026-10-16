# R23D-2B1G — Separate-process guardian LAB (not a host guardian)

## What is different from R23D-2B1F?

R23D-2B1F modeled an ordering contract using a pure in-memory state machine. This checkpoint introduces `scripts/guardian-v140-r23d2b1g-process-lab.py`, which is an **actual separate Python process** in integration tests. It consumes bounded, explicitly synthetic JSONL evidence over standard input, monitors a **real local monotonic clock** for the next input frame, rejects unexpected/replayed frames, and latches STOP on EOF, timeouts, SIGTERM/SIGINT and unsafe evidence. Tests exercise subprocess exit codes and output in addition to pure assertions.

This is NOT a systemd guardian, Docker watcher, nft kernel verifier, privilege isolation boundary, boot-fence, container kill/fence path or authorization for a Gateway runtime. A malicious/non-independent stdin producer could lie about every boolean. **No real host state is read.** No `sd_notify READY=1` or `WATCHDOG=1` is sent. No cleanup or restart is attempted on lab failure.

## Security properties of the laboratory protocol

- Explicit `--lab-only` required; no `--apply`, no host mode and no privileged commands.
- Epoch must be in `R23D_SYNTHETIC_...` namespace. Exact seven expected JSON keys only: epoch, sequence, guard attested, Docker identity attested, fence ready, synthetic-only, reboot detected. Additional/unexpected fields rejected (including potential secrets).
- Sequence strictly increments by 1; duplicate JSON keys rejected, boolean `true` required literally, reboot `false` required literally.
- 1–20 evidence frames with 100–3000 ms bounded monotonic deadline per complete frame, max 1024 bytes per frame, no indefinite waiting or timer driven by caller-controlled timestamps.
- Any missing frame, truncated JSON, pipe EOF, signal, false guard/fence identity or sequence replay exits nonzero with a fixed public error category. No raw evidence or secrets echoed.
- Successful lab output is exactly `LAB_EXERCISE_PASS_NO_RUNTIME_AUTHORIZATION`, after bounded frames. It does not issue canary start permission and does not remain as a watchdog.
- No host nfts/Docker/systemd APIs or network connections exist in this module. Production RC6/AIO/nginx/Trakt remain outside the lab.

## Example of expected CI exercise

`python3 -B -m unittest discover -s test -p 'test_v140_r23d2b1g_process_guardian_lab.py' -v`

The test suite launches child processes for normal two-frame completion, signal interruption, missing heartbeat, EOF, reboot, duplicate keys, malformed/excess frames, wrong epoch, stale/replayed sequence, and failure response without secret echo.

## Remaining implementation blockers

1. Develop a **genuine host-side evidence source** under independent privilege with exact Docker image/peer/network and fresh kernel nft JSON identity inspection. These assertions must be externally verified, not synthetic true booleans. The existing R23D-2A structural nft verifier can be reused, but it does not test effective INPUT/FORWARD packet behavior.
2. Protect the future guardian-to-canary control channel from replay and untrusted process impersonation; no actual unsafe subscriber may launch from a lab process's output.
3. Install a separately independent persistent **boot-time traffic fence before any Docker canary peer can exist**. No assumption that nft runtime policy survives reboot or daemon restart.
4. Implement an independently supervised fence/kill escalation with exact Docker ownership and watchdog crash recovery. A guardian process printing STOP has **no effect on traffic**.
5. Prove synthetic TCP allowed + denied paths, IPv6 bypass restrictions, Docker restarts, guardian crash, nft table flush and host reboot in a controlled isolated environment. Never run a real AIO/Trakt user session here.
6. Run protected pre/postflight and audit CI; keep all PRs draft until independent security qualification.

**Host safety:** tests launch only local Python lab children and don't use sudo, Docker, nft, systemctl, or host network. A successful check is **process-lab qualification only**.
