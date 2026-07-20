#!/usr/bin/env bash
# Push the current branch to ALL three AetherSeed mirrors, individually.
# Adds any missing remotes first. One failing remote does not stop the others.
#
# Usage: ./scripts/push_all.sh [branch]   (defaults to the current branch)
set -uo pipefail

declare -A REMOTES=(
  [an3s]="https://github.com/AN3S-CREATE/Vralogix-AetherSeed-OSINT.git"
  [veralogix]="https://github.com/veralogix-group-innovation/Vralogix-AetherSeed-OSINT.git"
  [catalyst]="https://github.com/VeralogixCatalyst/Vralogix-AetherSeed-OSINT.git"
)

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
echo "Pushing branch '$BRANCH' to all mirrors..."

failed=()
for name in "${!REMOTES[@]}"; do
  url="${REMOTES[$name]}"
  # Ensure the remote exists and points at the right URL.
  if git remote get-url "$name" >/dev/null 2>&1; then
    git remote set-url "$name" "$url"
  else
    git remote add "$name" "$url"
  fi

  echo "  → $name ($url)"
  if ! git push "$name" "$BRANCH"; then
    echo "    ! push to '$name' FAILED"
    failed+=("$name")
  fi
done

if [ "${#failed[@]}" -ne 0 ]; then
  echo "Completed with failures: ${failed[*]}" >&2
  exit 1
fi
echo "All mirrors updated."
