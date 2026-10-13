#!/usr/bin/env bash
# Read-only image provenance gate for the existing HomeDocker deployment.
# No pulls, restarts, updates, environment or credential output.
set -euo pipefail

echo "HOMEDOCKER_STABLE_IMAGE_PROVENANCE_V1"
for name in trakt-bridge aiostreams; do
  if ! docker container inspect "$name" >/dev/null 2>&1; then
    echo "STOP: required production container missing: $name" >&2
    exit 2
  fi
  config_image=$(docker container inspect "$name" --format '{{.Config.Image}}')
  image_id=$(docker container inspect "$name" --format '{{.Image}}')
  image_created=$(docker image inspect "$image_id" --format '{{.Created}}')
  repo_digests=$(docker image inspect "$image_id" --format '{{json .RepoDigests}}')
  case "$config_image" in
    *@sha256:*) reference_type="IMMUTABLE_DIGEST" ;;
    *) reference_type="MUTABLE_TAG_OR_OTHER" ;;
  esac
  printf 'SERVICE=%s\n' "$name"
  printf ' CONFIG_IMAGE=%s\n' "$config_image"
  printf ' CONFIG_REFERENCE_TYPE=%s\n' "$reference_type"
  printf ' RUNNING_IMAGE_ID=%s\n' "$image_id"
  printf ' LOCAL_IMAGE_CREATED=%s\n' "$image_created"
  printf ' LOCAL_REPO_DIGEST_CANDIDATES=%s\n' "$repo_digests"
done
echo "CHECK=READ_ONLY_NO_PULL_NO_RESTART"
echo "NOTE=RepoDigests are local Docker metadata; validate candidate repo/digest points to the running image ID before pinning Compose."
