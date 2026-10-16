#!/usr/bin/env bash
# R23D-2B1S: actual QEMU Ubuntu GUEST kernel reboot + GUEST systemd/Docker
# cold-start hold. Disposable GitHub-hosted runner ONLY. NO host reboot.
# Deliberately does not assert full Gateway/NAT/privileged-bypass qualification.
set -euo pipefail
if [[ "$#" -ne 1 || "${1:-}" != "--ci-ephemeral-only" ||
  "${GITHUB_ACTIONS:-}" != "true" || "${CI:-}" != "true" ||
  "${RUNNER_OS:-}" != "Linux" ||
  "${RUNNER_ENVIRONMENT:-}" != "github-hosted" || "$(id -u)" -ne 0 ]];then
  echo "R23D2B1S_STOP_NOT_DISPOSABLE_GITHUB_RUNNER" >&2;exit 3
fi
for bin in qemu-system-x86_64 qemu-img cloud-localds ssh scp ssh-keygen \
  curl python3 sha256sum timeout ss;do
  command -v "$bin" >/dev/null || {
    echo "R23D2B1S_STOP_MISSING_VM_DEPENDENCY" >&2;exit 4;
  }
done
[[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]] || {
  echo "R23D2B1S_BLOCKED_NO_USABLE_KVM_NOT_A_PASS" >&2;exit 4;
}
PORT=12722
if ss -ltnH | grep -q "127.0.0.1:$PORT ";then
  echo "R23D2B1S_STOP_HOST_LOOPBACK_PORT_COLLISION" >&2;exit 4
fi
TMP="$(mktemp -d)"
chmod 700 "$TMP"
VM_PID=""
cleanup(){
  local rc=$?
  trap - EXIT INT TERM
  if [[ -f "$TMP/vm.pid" ]];then
    VM_PID="$(cat "$TMP/vm.pid")"
    if [[ "$VM_PID" =~ ^[1-9][0-9]*$ ]];then
      kill "$VM_PID" >/dev/null 2>&1 || true
      for _ in $(seq 1 20);do
        if ! kill -0 "$VM_PID" >/dev/null 2>&1;then break;fi
        sleep 0.1
      done
      kill -KILL "$VM_PID" >/dev/null 2>&1 || true
    fi
  fi
  rm -rf -- "$TMP"
  if ((rc==0));then
    echo "R23D2B1S_QEMU_VM_OWNED_STORAGE_AND_PROCESS_CLEANUP_PASS"
  else
    echo "R23D2B1S_QEMU_DISPOSABLE_BOOT_LAB_STOP" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Published Ubuntu 24.04 cloud guest. Both files are from the same official
# HTTPS release directory; the image must match this downloaded SHA256SUMS.
# No user host credentials, volumes, SSH agent or HomeDocker network.
URL="https://cloud-images.ubuntu.com/releases/noble/release"
IMG="ubuntu-24.04-server-cloudimg-amd64.img"
curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
  "$URL/SHA256SUMS" -o "$TMP/SHA256SUMS"
curl -fsSL --retry 3 --connect-timeout 15 --max-time 400 \
  "$URL/$IMG" -o "$TMP/$IMG"
python3 -B - "$TMP/SHA256SUMS" "$IMG" >"$TMP/checksum" <<'PY'
import pathlib,re,sys
lines=pathlib.Path(sys.argv[1]).read_text().splitlines()
matches=[]
for line in lines:
    m=re.match(r"^([0-9a-fA-F]{64})\s+\*?(.*)$",line)
    if m and pathlib.PurePosixPath(m.group(2)).name==sys.argv[2]:
        matches.append(m.group(1).lower())
if len(matches)!=1:raise SystemExit("R23D2B1S_IMAGE_CHECKSUM_UNAVAILABLE")
print(f"{matches[0]}  {pathlib.Path(sys.argv[1]).parent/sys.argv[2]}")
PY
sha256sum -c "$TMP/checksum" >/dev/null
echo "R23D2B1S_OFFICIAL_GUEST_IMAGE_SHA256_VERIFIED_PASS"
qemu-img convert -f qcow2 -O qcow2 "$TMP/$IMG" "$TMP/guest.qcow2"
qemu-img resize "$TMP/guest.qcow2" 12G >/dev/null

ssh-keygen -q -t ed25519 -N "" -f "$TMP/guestkey"
PUB="$(cat "$TMP/guestkey.pub")"
cat >"$TMP/user-data" <<EOF
#cloud-config
hostname: r23d2b1s-guest
manage_etc_hosts: true
ssh_pwauth: false
disable_root: true
users:
  - default
  - name: b1s
    lock_passwd: true
    groups: [sudo]
    shell: /bin/bash
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    ssh_authorized_keys:
      - $PUB
package_update: true
packages:
  - docker.io
runcmd:
  - [systemctl, enable, --now, docker]
final_message: "R23D2B1S_GUEST_CLOUD_INIT_COMPLETE"
EOF
cat >"$TMP/meta-data" <<EOF
instance-id: r23d2b1s-$$
local-hostname: r23d2b1s-guest
EOF
cloud-localds "$TMP/seed.iso" "$TMP/user-data" "$TMP/meta-data"
# QEMU guest is completely separate from host Docker. Its only host-facing
# port is guest SSH on 127.0.0.1 loopback. No exposed test media ports.
qemu-system-x86_64 -enable-kvm -machine q35 -m 2560 -smp 2 \
  -drive "file=$TMP/guest.qcow2,if=virtio,format=qcow2" \
  -drive "file=$TMP/seed.iso,if=virtio,media=cdrom" \
  -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$PORT-:22" \
  -device virtio-net-pci,netdev=net0 \
  -nographic -serial "file:$TMP/guest-serial.log" \
  -daemonize -pidfile "$TMP/vm.pid"
VM_PID="$(cat "$TMP/vm.pid")"
[[ "$VM_PID" =~ ^[1-9][0-9]*$ ]]
SSH=(ssh -i "$TMP/guestkey" -p "$PORT"
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$TMP/knownhosts"
  -o IdentitiesOnly=yes -o ConnectTimeout=3
  -o ServerAliveInterval=4
  -o ServerAliveCountMax=2
  b1s@127.0.0.1)
SCP=(scp -i "$TMP/guestkey" -P "$PORT"
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$TMP/knownhosts"
  -o IdentitiesOnly=yes -o ConnectTimeout=3)

guest(){
  "${SSH[@]}" "$@"
}
wait_ssh(){
  for _ in $(seq 1 150);do
    if guest 'true' >/dev/null 2>&1;then return 0;fi
    sleep 2
  done
  return 1
}
wait_ssh || {
  echo "R23D2B1S_GUEST_SSH_NEVER_BOOTED" >&2;exit 5
}
timeout 660 "${SSH[@]}" sudo cloud-init status --wait >/dev/null
guest sudo docker info >/dev/null
BOOT1="$(guest cat /proc/sys/kernel/random/boot_id | tr -d '\r')"
[[ "$BOOT1" =~ ^[0-9a-f-]{36}$ ]]
echo "R23D2B1S_REAL_QEMU_GUEST_INITIAL_KERNEL_BOOT_PASS"

# No outside pull required: import a synthetic image with no runnable code.
# docker create persists restart=no + network none across guest reboot.
guest 'set -e; mkdir -p /tmp/b1s-image && printf ci > /tmp/b1s-image/marker &&
  tar -C /tmp/b1s-image -cf /tmp/b1s-image.tar . &&
  sudo docker import /tmp/b1s-image.tar b1s-guest-image:ci >/dev/null &&
  sudo docker create --name r23d2b1s-guest-gateway --network none --restart no --read-only --cap-drop ALL b1s-guest-image:ci /bin/false >/dev/null'

# Real installed guest systemd unit only INSPECTS exact Docker state.
# This is evidence of boot ordering, not a privileged API interlock.
cat >"$TMP/guest-boot-verify.sh" <<'GUEST'
#!/bin/bash
set -euo pipefail
/usr/bin/docker info >/dev/null
NAME="r23d2b1s-guest-gateway"
[[ "$(/usr/bin/docker inspect -f '{{.State.Running}}' "$NAME")" == "false" ]]
[[ "$(/usr/bin/docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$NAME")" == "no" ]]
[[ "$(/usr/bin/docker inspect -f '{{.HostConfig.NetworkMode}}' "$NAME")" == "none" ]]
/usr/bin/docker inspect -f '{{json .NetworkSettings.Networks}}' "$NAME" |
  /usr/bin/python3 -c 'import json,sys
assert set(json.load(sys.stdin) or {}).issubset({"none"})'
ID="$(cat /proc/sys/kernel/random/boot_id)"
printf '%s\n' "$ID" >/run/r23d2b1s-verified-boot-id
echo "R23D2B1S_GUEST_REAL_BOOT_DOCKER_DEFAULT_NETWORK_HOLD_PASS"
GUEST
chmod 755 "$TMP/guest-boot-verify.sh"
"${SCP[@]}" "$TMP/guest-boot-verify.sh" b1s@127.0.0.1:/tmp/b1s-boot-verify.sh >/dev/null
guest 'sudo install -m 755 /tmp/b1s-boot-verify.sh /usr/local/sbin/r23d2b1s-boot-verify.sh'
cat >"$TMP/guest-unit.service" <<'UNIT'
[Unit]
Description=R23D-2B1S real guest boot-order Docker default-network hold
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/r23d2b1s-boot-verify.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
"${SCP[@]}" "$TMP/guest-unit.service" b1s@127.0.0.1:/tmp/r23d2b1s-boot-verify.service >/dev/null
guest 'sudo mv /tmp/r23d2b1s-boot-verify.service /etc/systemd/system/ &&
  sudo systemctl daemon-reload &&
  sudo systemctl enable --now r23d2b1s-boot-verify.service'
[[ "$(guest sudo cat /run/r23d2b1s-verified-boot-id | tr -d '\r')" == "$BOOT1" ]]
echo "R23D2B1S_GUEST_INSTALLED_SYSTEMD_DOCKER_ORDER_PASS"

# Actual Linux VM/kernel reboot, not a Docker daemon restart or a host reboot.
guest 'sudo systemctl reboot' >/dev/null 2>&1 || true
CHANGED=0
for _ in $(seq 1 200);do
  ID="$(guest cat /proc/sys/kernel/random/boot_id 2>/dev/null | tr -d '\r' || true)"
  if [[ "$ID" =~ ^[0-9a-f-]{36}$ && "$ID" != "$BOOT1" ]];then
    BOOT2="$ID";CHANGED=1;break
  fi
  sleep 2
done
if ((CHANGED!=1));then
  echo "R23D2B1S_TRUE_GUEST_KERNEL_BOOT_ID_NOT_CHANGED_STOP" >&2;exit 5
fi
[[ "$BOOT2" != "$BOOT1" ]]
echo "R23D2B1S_ACTUAL_VM_KERNEL_REBOOT_BOOT_ID_CHANGED_PASS"
for _ in $(seq 1 60);do
  if guest 'systemctl is-active --quiet r23d2b1s-boot-verify.service' >/dev/null 2>&1;then
    break
  fi
  sleep 2
done
guest 'sudo systemctl is-active --quiet docker'
guest 'systemctl is-active --quiet r23d2b1s-boot-verify.service'
AFTER="$(guest sudo cat /run/r23d2b1s-verified-boot-id | tr -d '\r')"
[[ "$AFTER" == "$BOOT2" ]]
guest 'sudo docker inspect -f "{{.HostConfig.NetworkMode}}" r23d2b1s-guest-gateway' |
  grep -Fxq none
guest 'sudo docker inspect -f "{{.State.Running}}" r23d2b1s-guest-gateway' |
  grep -Fxq false
echo "R23D2B1S_GUEST_COLD_BOOT_SYSTEMD_ORDER_AND_DOCKER_HOLD_PASS"
echo "R23D2B1S_TRUE_VM_REBOOT_SMOKE_PASS_NOT_A_PRODUCTION_GATEWAY_QUALIFICATION"
