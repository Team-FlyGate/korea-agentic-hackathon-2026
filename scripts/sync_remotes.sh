#!/usr/bin/env bash
# Keep the personal repository and the team organization repository holding the same history.
#
# We deliberately kept both rather than transferring one into the other:
#   personal  github.com/kakyungkim/korea-agentic-hackathon-2026      (origin, also the fetch source)
#   team      github.com/Team-FlyGate/korea-agentic-hackathon-2026     (team)
# Transferring would have removed the repository from the personal account and broken the URL
# already written into four documents and the submission PDF. Two remotes cost one extra push
# configuration and nothing else.
#
# `origin` carries two push URLs, so a plain `git push` reaches both. This script covers the
# other direction: a teammate pushing straight to the organization copy, which the dual-push
# setup cannot see.
#
# Usage:
#   scripts/sync_remotes.sh          # report only
#   scripts/sync_remotes.sh --push   # report, then bring both to the local commit
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH="${BRANCH:-main}"
git fetch -q origin "$BRANCH"
git fetch -q team "$BRANCH"

local_sha=$(git rev-parse "$BRANCH")
o_sha=$(git rev-parse "origin/$BRANCH")
t_sha=$(git rev-parse "team/$BRANCH")

printf '로컬   %s\n개인   %s\n팀     %s\n' "${local_sha:0:12}" "${o_sha:0:12}" "${t_sha:0:12}"

if [ "$o_sha" = "$t_sha" ] && [ "$o_sha" = "$local_sha" ]; then
  echo "세 곳이 같다. 할 일 없음."
  exit 0
fi

# Stop rather than overwrite when the team copy holds commits we do not have. A force-free push
# would fail anyway, and a silent rewrite would lose a teammate's work.
ahead_team=$(git rev-list --count "$local_sha..$t_sha")
if [ "$ahead_team" -gt 0 ]; then
  echo "팀 저장소에만 있는 커밋 ${ahead_team}건이 있다. 먼저 가져와 합쳐라:"
  echo "  git pull --rebase team $BRANCH"
  exit 1
fi

if [ "${1:-}" = "--push" ]; then
  git push origin "$BRANCH"   # both push URLs are attached to origin, so this reaches each copy
  echo "양쪽을 맞췄다."
else
  echo "맞추려면 --push 를 붙여 다시 돌려라."
fi
