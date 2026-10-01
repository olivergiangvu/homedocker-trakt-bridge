#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "ERROR: run inside a Git repository" >&2
  exit 2
fi
cd "$ROOT"

fail=0
warn=0

section() { printf '\n== %s ==\n' "$1"; }

section "Repository"
echo "root=$ROOT"
echo "head=$(git rev-parse --short HEAD)"
echo "commits=$(git rev-list --all --count)"

section "Sensitive file names in Git history"
mapfile -t sensitive_files < <(
  git log --all --name-only --pretty=format: \
    | sed '/^$/d' \
    | sort -u \
    | grep -Ei '(^|/)(\.env|id_(rsa|ed25519)|[^/]+\.(pem|key|p12|pfx|sqlite|sqlite3|db|db-wal|db-shm))$' \
    || true
)
if ((${#sensitive_files[@]})); then
  printf '%s\n' "${sensitive_files[@]}"
  echo "FAIL: sensitive-looking files have existed in Git history"
  fail=1
else
  echo "PASS"
fi

section "Private-key material in Git history"
private_hits=0
while IFS= read -r rev; do
  if git grep -nE 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' "$rev" -- . 2>/dev/null; then
    private_hits=1
  fi
done < <(git rev-list --all)
if ((private_hits)); then
  echo "FAIL: private-key material detected"
  fail=1
else
  echo "PASS"
fi

section "Credential-shaped assignments in current tree"
# These are review-only because examples and CI fixtures intentionally contain
# placeholder/test values. Real secrets must never appear here.
git grep -nE '(TRAKT_CLIENT_SECRET|BRIDGE_SECRET_KEY|ADMIN_KEY)=[^[:space:]]{20,}' -- . \
  ':!CHANGELOG.md' 2>/dev/null || true
echo "REVIEW: only placeholders or fixed test fixtures should appear above"

section "Profile manifest-shaped URLs in Git history"
manifest_hits=0
while IFS= read -r rev; do
  if git grep -nE 'https?://[^[:space:]]+/u/[0-9a-fA-F-]{20,}/[A-Za-z0-9_-]{20,}' "$rev" -- . 2>/dev/null; then
    manifest_hits=1
  fi
done < <(git rev-list --all)
if ((manifest_hits)); then
  echo "FAIL: profile-scoped manifest-shaped URL detected"
  fail=1
else
  echo "PASS"
fi

section "Local forbidden strings"
FORBIDDEN_FILE="${PRE_PUBLIC_FORBIDDEN_FILE:-.pre-public-forbidden}"
if [[ -f "$FORBIDDEN_FILE" ]]; then
  while IFS= read -r needle || [[ -n "$needle" ]]; do
    [[ -z "$needle" || "$needle" == \#* ]] && continue
    found=0
    while IFS= read -r rev; do
      if git grep -nF -e "$needle" "$rev" -- . 2>/dev/null; then
        found=1
      fi
    done < <(git rev-list --all)
    if ((found)); then
      echo "FAIL: forbidden string found in history"
      fail=1
    else
      echo "PASS: forbidden string absent from history"
    fi
  done < "$FORBIDDEN_FILE"
else
  echo "SKIP: $FORBIDDEN_FILE not present"
  echo "Tip: create it locally with one private hostname, IP, port, username or token fragment per line."
fi

section "Tracked generated/runtime files in current tree"
tracked_runtime="$(git ls-files | grep -Ei '(^|/)(\.env|[^/]+\.(db|db-wal|db-shm|sqlite|sqlite3))$' || true)"
if [[ -n "$tracked_runtime" ]]; then
  echo "$tracked_runtime"
  echo "FAIL"
  fail=1
else
  echo "PASS"
fi

section "Result"
if ((fail)); then
  echo "PRE_PUBLIC_AUDIT=FAIL"
  echo "Do not make the repository public until every hit is reviewed and any real secret is rotated and removed from history."
  exit 1
fi

echo "PRE_PUBLIC_AUDIT=PASS"
echo "This is a focused repository audit, not a substitute for GitHub secret scanning or a dedicated secret scanner."
