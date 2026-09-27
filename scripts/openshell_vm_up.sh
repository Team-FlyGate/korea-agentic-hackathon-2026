#!/usr/bin/env bash
# Bring up the whole OpenShell stack inside a Linux VM in one run: Docker, OpenShell, the
# sandbox image, the model provider, the sandbox itself, and finally the policy. Two VM
# backends are supported, Colima (default) and Multipass.
#
# Usage:  scripts/openshell_vm_up.sh [pharmasignal|base|flydock]   (default: pharmasignal)
# Optional environment variables:
#   VM_BACKEND=colima|multipass   (default: colima)
#   VM_NAME=openshell  VM_CPUS=2  VM_MEM=6G  VM_DISK=30G      (multipass backend only)
#   SANDBOX_NAME=<policy>   SANDBOX_CPU=2  SANDBOX_MEM=3Gi
#   OPENSHELL_DRIVER=docker  (gateway compute driver; autodetection fails on Colima, so set it)
#   NVIDIA_API_KEY=...  also read from .env at the repo root; without it the provider step is skipped
#   OPENSHELL_VERSION=...   (passed straight to install.sh; defaults to the latest stable release)
#
# Every step is safe to re-run: anything already in place is skipped. A failure stops the run
# where it happened (set -e) rather than leaving a half-built stack that looks finished.
#
# Verified run: all eight steps passed on the Colima backend on 2026-09-25 (OpenShell 0.0.116,
# Docker 29.5.2, Ubuntu 24.04, kernel 6.8.0-117). The Multipass backend could not be exercised
# because qemu-img crashes on this Intel Mac [unverified]. The full log is in
# docs/notes/openshell-setup.md.
#
# Reference documents (checked 2026-09-24) and measurements (2026-09-25)
#   OpenShell install.sh:      https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh
#   support matrix (kernel requirements): https://github.com/NVIDIA/OpenShell/blob/main/docs/reference/support-matrix.mdx
#   sandbox management (flags):           https://github.com/NVIDIA/OpenShell/blob/main/docs/sandboxes/manage-sandboxes.mdx
#   Docker apt install:                   https://docs.docker.com/engine/install/ubuntu/
#   Colima:                    https://github.com/abiosoft/colima

set -euo pipefail

POLICY="${1:-pharmasignal}"
case "$POLICY" in pharmasignal|base|flydock) ;; *) echo "정책은 pharmasignal | base | flydock 중 하나" >&2; exit 2;; esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VM_BACKEND="${VM_BACKEND:-colima}"
VM_NAME="${VM_NAME:-openshell}"
VM_CPUS="${VM_CPUS:-2}"
VM_MEM="${VM_MEM:-6G}"
VM_DISK="${VM_DISK:-30G}"
SANDBOX_NAME="${SANDBOX_NAME:-$POLICY}"
SANDBOX_CPU="${SANDBOX_CPU:-2}"
SANDBOX_MEM="${SANDBOX_MEM:-3Gi}"
OPENSHELL_DRIVER="${OPENSHELL_DRIVER:-docker}"
IMAGE_TAG="hackathon-sandbox:latest"
PROFILE_ID="nvidia-hackathon"
PROVIDER_NAME="nvidia"

log()  { printf '\n\033[1;34m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '\033[1;31m오류: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- VM backend abstraction
# vm <shell command>  : run it inside the VM as a login shell, passing stdin straight through.
case "$VM_BACKEND" in
  colima)
    command -v colima >/dev/null 2>&1 || die "colima 가 없다. 먼저 설치한다:  brew install colima"
    vm() { colima ssh -- bash -lc "$*"; }
    ;;
  multipass)
    command -v multipass >/dev/null 2>&1 || die "multipass 가 없다. 먼저 설치한다:  brew install --cask multipass"
    vm() { multipass exec "$VM_NAME" -- bash -lc "$*"; }
    ;;
  *)
    die "VM_BACKEND 는 colima 또는 multipass"
    ;;
esac

# Read NVIDIA_API_KEY from .env, with an already-exported variable winning. The value is never
# printed, so a shared terminal or a pasted log cannot leak it.
if [ -z "${NVIDIA_API_KEY:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  NVIDIA_API_KEY="$(grep -E '^NVIDIA_API_KEY=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]' || true)"
fi
case "${NVIDIA_API_KEY:-}" in ""|nvapi-xxxxxxxx) NVIDIA_API_KEY="";; esac

# ---------------------------------------------------------------- 1. VM
log "1/8 VM 확인 (백엔드: $VM_BACKEND)"
if [ "$VM_BACKEND" = colima ]; then
  # Colima expects the VM to exist already. Check its state instead of creating one here.
  if ! colima status >/dev/null 2>&1; then
    cat >&2 <<'MSG'
colima VM 이 돌고 있지 않다. 먼저 띄운다:

  colima start --vm-type vz --cpu 2 --memory 5 --disk 30

(Intel Mac 은 Multipass 의 qemu-img 가 세그멘테이션 오류로 죽어 Colima 를 쓴다.
 자세한 내용은 docs/TROUBLESHOOTING.md 3번 항목.)
MSG
    exit 1
  fi
  colima status 2>&1 | sed 's/^/  /'
else
  if multipass info "$VM_NAME" >/dev/null 2>&1; then
    state="$(multipass info "$VM_NAME" | awk '/^State:/{print $2}')"
    echo "이미 있음 (상태: $state)"
    case "$state" in Running) ;; *) multipass start "$VM_NAME";; esac
  else
    multipass launch 24.04 --name "$VM_NAME" --cpus "$VM_CPUS" --memory "$VM_MEM" --disk "$VM_DISK" --timeout 900
  fi
fi
vm 'echo "VM OK: $(uname -m) $(lsb_release -ds 2>/dev/null || head -1 /etc/os-release)"'

# The home directory differs per backend (multipass: /home/ubuntu, colima: /home/<user>.guest).
VM_HOME="$(vm 'printf %s "$HOME"')"
VM_WORK="$VM_HOME/hackathon"
echo "VM 작업 디렉터리: $VM_WORK"

# ---------------------------------------------------------------- 2. kernel feature check
log "2/8 커널 기능 점검 (Landlock ABI 3+ = Linux 6.2+, seccomp, netns)"
vm '
set -e
kver=$(uname -r); echo "kernel: $kver"
major=${kver%%.*}; rest=${kver#*.}; minor=${rest%%.*}
if [ "$major" -lt 6 ] || { [ "$major" -eq 6 ] && [ "$minor" -lt 2 ]; }; then
  echo "커널 $kver 은 6.2 미만이라 Landlock ABI 3 을 제공하지 않는다"; exit 1; fi
if grep -qw landlock /sys/kernel/security/lsm; then echo "landlock LSM: 활성 ($(cat /sys/kernel/security/lsm))";
else echo "landlock LSM 이 활성 LSM 목록에 없다: $(cat /sys/kernel/security/lsm)"; exit 1; fi
cfg=/boot/config-$kver
if [ -r "$cfg" ]; then
  grep -E "^CONFIG_(SECURITY_LANDLOCK|SECCOMP|SECCOMP_FILTER|NET_NS|USER_NS)=y" "$cfg" || { echo "커널 설정에 필요한 항목이 빠져 있다"; exit 1; }
else echo "(경고) $cfg 를 읽을 수 없어 seccomp/netns 설정은 확인하지 못함 [unverified]"; fi
'

# ---------------------------------------------------------------- 3. Docker
log "3/8 Docker Engine (OpenShell 은 28.0 이상 요구)"
if vm 'command -v docker >/dev/null 2>&1'; then
  vm 'docker --version'
else
  [ "$VM_BACKEND" = colima ] && die "colima VM 에 docker 가 없다. 'colima start --runtime docker' 로 다시 띄운다"
  vm '
set -e
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<SRC
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
SRC
sudo apt-get update -qq
sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
'
fi
vm 'docker version --format "Docker client {{.Client.Version}} / server {{.Server.Version}}"' \
  || die "VM 사용자로 docker 소켓 접근 실패"
vm 'v=$(docker version --format "{{.Server.Version}}"); [ "${v%%.*}" -ge 28 ] || { echo "Docker $v 는 28 미만"; exit 1; }'

# The gateway runs as a systemd --user service and opens the Docker socket, which needs two
# things to be true:
#   (1) the user belongs to the docker group, and
#   (2) the user's systemd manager process itself carries that group.
# On Colima the manager can come up before the group is applied, and then the gateway dies with
# "failed to query Docker daemon version ... client error (Connect)". Seen in practice, which is
# why the restart below is not optional.
log "3b/8 사용자 systemd 매니저가 docker 그룹을 들고 있는지"
vm '
set -e
id -nG | grep -qw docker || { sudo usermod -aG docker "$USER"; echo "docker 그룹 추가"; }
sudo loginctl enable-linger "$USER" >/dev/null 2>&1 || true
dgid=$(getent group docker | cut -d: -f3)
mpid=$(pgrep -u "$USER" -f "systemd --user" | head -1 || true)
if [ -n "$mpid" ] && ! grep -q "^Groups:.*\b${dgid}\b" /proc/$mpid/status; then
  echo "사용자 systemd 매니저($mpid)에 docker 그룹($dgid)이 없어 다시 띄운다"
  sudo systemctl restart "user@$(id -u).service"
  sleep 5
fi
mpid=$(pgrep -u "$USER" -f "systemd --user" | head -1 || true)
echo "user manager pid=$mpid groups=$(grep ^Groups: /proc/$mpid/status | cut -f2-)  docker gid=$dgid"
'

# ---------------------------------------------------------------- 4. OpenShell
log "4/8 OpenShell (deb 패키지 + 사용자 서비스 openshell-gateway, driver=$OPENSHELL_DRIVER)"
# The gateway autodetects its compute driver in the order Kubernetes, Podman, Docker, and on a
# Colima VM it finds none of them and exits. Pin the choice through the EnvironmentFile at
# ~/.config/openshell/gateway.env. It has to be written before installation, because the install
# script starts the gateway itself.
vm "mkdir -p ~/.config/openshell && printf 'OPENSHELL_DRIVERS=%s\n' '$OPENSHELL_DRIVER' > ~/.config/openshell/gateway.env"
if vm 'command -v openshell >/dev/null 2>&1'; then
  vm 'echo "이미 설치됨: $(openshell --version)"'
else
  vm "curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh -o /tmp/openshell-install.sh && OPENSHELL_VERSION='${OPENSHELL_VERSION:-}' sh /tmp/openshell-install.sh"
fi
vm '
set -e
systemctl --user daemon-reload
systemctl --user enable openshell-gateway >/dev/null 2>&1 || true
systemctl --user is-active --quiet openshell-gateway || { systemctl --user restart openshell-gateway; sleep 6; }
systemctl --user is-active --quiet openshell-gateway || {
  echo "게이트웨이가 뜨지 않았다. 최근 로그:"; journalctl --user -u openshell-gateway --no-pager -n 30; exit 1; }
echo "openshell-gateway: active"
'
# Register the local gateway if the CLI does not know it yet, which happens when a previous
# install run stopped midway.
vm 'openshell gateway list 2>/dev/null | grep -q 127.0.0.1:17670 || openshell gateway add https://127.0.0.1:17670 --local --name openshell'
vm 'openshell status'

# ---------------------------------------------------------------- 5. copy policies, build image
log "5/8 정책 파일 전송 → $VM_WORK, 샌드박스 이미지 $IMAGE_TAG 빌드"
vm "mkdir -p $VM_WORK"
# Strip the extended attributes macOS tar adds; GNU tar inside the VM warns about them.
COPYFILE_DISABLE=1 tar --no-xattrs -C "$REPO_ROOT" -cf - policies scripts | vm "tar -C $VM_WORK -xf -"
vm "ls $VM_WORK/policies"
if vm "docker image inspect $IMAGE_TAG >/dev/null 2>&1"; then
  echo "이미지 있음. 다시 빌드하려면 VM 에서: docker rmi $IMAGE_TAG"
else
  vm "docker build -t $IMAGE_TAG $VM_WORK/policies/sandbox-image"
fi

# ---------------------------------------------------------------- 6. provider (NVIDIA key)
log "6/8 provider profile '$PROFILE_ID' import, provider '$PROVIDER_NAME' 생성"
# lint treats an id that already exists as an error (measured), so check for the import first.
if vm "openshell provider list-profiles 2>/dev/null | grep -qw $PROFILE_ID"; then
  echo "profile 이미 import 됨 (다시 넣으려면 VM 에서: openshell provider profile delete $PROFILE_ID)"
else
  vm "openshell provider profile lint -f $VM_WORK/policies/nvidia-provider-profile.yaml"
  vm "openshell provider profile import -f $VM_WORK/policies/nvidia-provider-profile.yaml"
fi
PROVIDER_FLAG="--no-auto-providers"
if [ -n "$NVIDIA_API_KEY" ]; then
  if vm "openshell provider list 2>/dev/null | awk '{print \$1}' | grep -qx $PROVIDER_NAME"; then
    echo "provider '$PROVIDER_NAME' 이미 있음"
  else
    # Never pass the key as a command-line argument. Send it on stdin into an environment
    # variable and give --credential the variable's name rather than its value, so the secret
    # stays out of ps output and shell history.
    printf '%s\n' "$NVIDIA_API_KEY" | vm \
      'read -r k; export NVIDIA_API_KEY="$k"; openshell provider create --name '"$PROVIDER_NAME"' --type '"$PROFILE_ID"' --credential NVIDIA_API_KEY'
  fi
  PROVIDER_FLAG="--provider $PROVIDER_NAME"
else
  echo "NVIDIA_API_KEY 가 없어 provider 생성을 건너뛴다(.env 에 넣고 다시 실행하면 붙는다). 샌드박스는 --no-auto-providers 로 만든다."
fi

# ---------------------------------------------------------------- 7. sandbox
log "7/8 샌드박스 '$SANDBOX_NAME' (정책 $POLICY.yaml, --cpu $SANDBOX_CPU --memory $SANDBOX_MEM)"
if vm "openshell sandbox list --names 2>/dev/null | grep -qx '$SANDBOX_NAME'"; then
  echo "샌드박스 이미 있음. static 구역(filesystem/landlock/process)을 바꿨다면: openshell sandbox delete $SANDBOX_NAME 후 재실행"
else
  vm "cd $VM_WORK && openshell sandbox create --name $SANDBOX_NAME --from $IMAGE_TAG \
        --policy policies/$POLICY.yaml --cpu $SANDBOX_CPU --memory $SANDBOX_MEM $PROVIDER_FLAG --detach -- sleep infinity"
fi

# ---------------------------------------------------------------- 8. apply policy (full replace) and verify
log "8/8 정책 적용: openshell policy set $SANDBOX_NAME --policy policies/$POLICY.yaml --wait"
vm "cd $VM_WORK && openshell policy set $SANDBOX_NAME --policy policies/$POLICY.yaml --wait"
vm "openshell policy get $SANDBOX_NAME --full"
vm "openshell sandbox list"

cat <<MSG

완료. 다음 단계:
  scripts/openshell_smoke.sh $POLICY            # 허용/차단/쓰기 확인 → eval/results/openshell_smoke.txt
  colima ssh                                     # VM 접속 (multipass 백엔드면 multipass shell $VM_NAME)
  openshell logs $SANDBOX_NAME --tail            # (VM 안) 실시간 감사 로그
  openshell sandbox exec -n $SANDBOX_NAME -- <cmd>   # (VM 안) 샌드박스 안에서 명령 실행
MSG
