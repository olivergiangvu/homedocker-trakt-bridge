#!/usr/bin/env bash
# HomeDocker audit only: does not delete/restart/pull containers or images.
# NEVER prints container env, tokens, mount content, Compose env or DB content.
set -euo pipefail

echo "HOMEDOCKER_DOCKER_CLEANUP_INVENTORY_V1"
if ! docker info >/dev/null 2>&1; then
  echo "STOP: Docker CLI/daemon unavailable with current user. No cleanup attempted." >&2
  exit 2
fi

echo "=== DOCKER_DISK_SUMMARY ==="
docker system df || true

echo "=== RUNNING_CONTAINERS_AND_MEMORY ==="
docker ps --format 'name={{.Names}} status={{.Status}} image={{.Image}}'
docker stats --no-stream --format 'name={{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' || true

echo "=== STOPPED_CONTAINERS (CANDIDATES, NOT AUTO-SAFE) ==="
docker ps -a --filter status=exited --filter status=created \
  --format 'name={{.Names}} status={{.Status}} image={{.Image}}'
docker ps -a --filter status=dead \
  --format 'name={{.Names}} status={{.Status}} image={{.Image}}'

echo "=== DANGLING_IMAGES (MAY BE ROLLBACK ASSETS) ==="
docker image ls --filter dangling=true \
  --format 'image_id={{.ID}} size={{.Size}} created={{.CreatedSince}}'

echo "=== CONFIGURED_BRIDGE_AND_AIO_IMAGES ==="
for container in trakt-bridge aiostreams; do
  if docker inspect "$container" >/dev/null 2>&1; then
    docker inspect "$container" \
      --format 'name={{.Name}} configured_image={{.Config.Image}} running_image_id={{.Image}}'
  else
    echo "NOTE=$container not found by this exact name; verify stack ownership."
  fi
done

echo "=== DANGLING_VOLUMES (NOT SAFE TO PRUNE AUTOMATICALLY) ==="
docker volume ls --filter dangling=true --format 'volume={{.Name}} driver={{.Driver}}'

echo "=== CUSTOM_NETWORKS ==="
docker network ls --filter type=custom --format 'network={{.Name}} driver={{.Driver}}'

echo "=== BUILD_CACHE_FOOTPRINT ==="
if docker builder du >/dev/null 2>&1; then
  docker builder du 2>/dev/null | tail -n 30 || true
else
  echo "BUILD_CACHE=unavailable_without_builder_change"
fi

echo "=== RC7_HOME_SCRATCH_FILES (NAME/SIZE ONLY) ==="
for name in \
  audit-jellyfin-shelves.py audit-known-show-nextup-origin.py \
  audit-dual-imdb.py repair-stale-aio-nextup.py \
  monitor-identity-drift.py audit-stable-event-pulse.py \
  audit-rolling-aio-contract.py; do
  p="$HOME/$name"
  if [[ -f "$p" && ! -L "$p" ]]; then
    stat -c 'file=%n size_bytes=%s mode=%a' "$p"
  fi
done
if [[ -d "$HOME/__pycache__" ]]; then
  find "$HOME/__pycache__" -maxdepth 1 -type f \
    \( -name '*audit*jellyfin*.pyc' -o -name '*audit*nextup*.pyc' \
       -o -name '*repair*stale*aio*.pyc' -o -name '*monitor*identity*.pyc' \) \
    -printf 'pycache=%f size_bytes=%s\n'
fi
echo "=== DECISION ==="
echo "MODE=READ_ONLY_INVENTORY"
echo "DELETIONS=0"
echo "DO_NOT_RUN=docker_system_prune_-a_--volumes"
echo "NOTE=Share this output; verify ownership, mounts and rollback needs before exact targeted deletion."
