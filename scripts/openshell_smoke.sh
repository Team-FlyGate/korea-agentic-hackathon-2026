#!/usr/bin/env bash
# Smoke test for an OpenShell sandbox policy. It produces the evidence behind our claim that
# the policy is actually enforced, rather than merely written down:
#   (1) hosts on the allow list are reachable (the list differs per policy)
#   (2) hosts off it (example.com, github.com) are blocked
#   (3) writes succeed under read_write paths and fail everywhere else
#   (4) the uid inside the sandbox matches process.run_as_user, when the policy sets one
#   (6) DENIED lines are quoted verbatim from the gateway audit log
#   (7) a Python client passes certificate verification against a protocol: rest endpoint
# Expectations differ per policy file, so they are set together in the "targets" block below.
# Results land in eval/results/openshell_smoke.txt for pharmasignal and in
# eval/results/openshell_smoke_<policy>.txt otherwise. Any single failure exits 1.
#
# The probing client itself must be a binary the policy allows (the PROBE variable). flydock
# dropped /usr/bin/curl from its inference and data-source blocks and left only Python, so
#
# Usage:  scripts/openshell_smoke.sh [pharmasignal|base|flydock]   (default: pharmasignal)
# Optional environment variables:
#   VM_BACKEND=colima|multipass   (default colima; running inside the VM uses the local openshell)
#   VM_NAME=openshell             (multipass backend only)
#   SANDBOX_NAME=<policy>
#   OUT=<result file path>        (defaults as described above)
#
# Measured (2026-09-25, Colima with OpenShell 0.0.116): a blocked host shows up as the proxy
# answering CONNECT with 403 and curl exiting 56 ("curl: (56) CONNECT tunnel failed, response
# 403"). A blocked write comes from Landlock as "Permission denied" with exit code 2. Knowing
# the exact codes matters, because a test that only checks "it failed" would also pass when the
# sandbox is simply broken.

set -uo pipefail

POLICY="${1:-pharmasignal}"
case "$POLICY" in pharmasignal|base|flydock) ;; *) echo "정책은 pharmasignal | base | flydock" >&2; exit 2;; esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VM_BACKEND="${VM_BACKEND:-colima}"
VM_NAME="${VM_NAME:-openshell}"
SANDBOX_NAME="${SANDBOX_NAME:-$POLICY}"
OUT_DIR="$REPO_ROOT/eval/results"
# One result file per policy. pharmasignal keeps the original filename so earlier records stay
# where documents already cite them. OUT overrides both.
case "$POLICY" in
  pharmasignal) OUT="${OUT:-$OUT_DIR/openshell_smoke.txt}" ;;
  *)            OUT="${OUT:-$OUT_DIR/openshell_smoke_$POLICY.txt}" ;;
esac
mkdir -p "$OUT_DIR"

# ---------------------------------------------------------------- where the commands run
# Inside the VM (or any Linux with openshell installed) the local binary is used; on macOS the
# calls go through the selected VM backend.
# stdin must be tied to /dev/null. `openshell sandbox exec` forwards stdin into the sandbox, so
# anywhere without a terminal (background run, CI) it blocks forever waiting for EOF. Measured.
if command -v openshell >/dev/null 2>&1; then
  vm() { bash -lc "$*" </dev/null; }
  WHERE="local"
elif [ "$VM_BACKEND" = colima ] && command -v colima >/dev/null 2>&1; then
  vm() { colima ssh -- bash -lc "$*" </dev/null; }
  WHERE="colima"
elif [ "$VM_BACKEND" = multipass ] && command -v multipass >/dev/null 2>&1; then
  vm() { multipass exec "$VM_NAME" -- bash -lc "$*" </dev/null; }
  WHERE="multipass:$VM_NAME"
else
  echo "openshell 도 $VM_BACKEND 도 없다" >&2; exit 2
fi

# Run an sh command inside the sandbox. The payload is base64-wrapped so quoting survives the
# three shells it passes through.
sb() {
  local b64; b64="$(printf '%s' "$1" | base64 | tr -d '\n')"
  vm "openshell sandbox exec -n $SANDBOX_NAME --no-tty --timeout 90 -- sh -c 'echo $b64 | base64 -d | sh'" 2>&1
}

# Run a Python program inside the sandbox, wrapped the same way as sb for the same reason.
# The program is written to /tmp, which the policy lists under read_write.
sbpy() {
  local b64; b64="$(printf '%s' "$1" | base64 | tr -d '\n')"
  sb "echo $b64 | base64 -d > /tmp/openshell_probe.py && python3 /tmp/openshell_probe.py; r=\$?; rm -f /tmp/openshell_probe.py; exit \$r"
}

# A GET probe on the standard library alone. The image carries neither requests nor httpx, and
# a policy that does not allow pypi could not install them anyway. urllib reads http_proxy and
# https_proxy by itself, so no proxy wiring is needed here.
py_probe_src() {  # py_probe_src <url>  → "http=NNN rc=N" 한 줄을 출력하는 파이썬 소스
  cat <<PY
import urllib.request, urllib.error
req = urllib.request.Request("$1", method="GET",
                             headers={"User-Agent": "openshell-smoke/1.0"})
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        print("http=%d rc=0" % r.status)
except urllib.error.HTTPError as e:
    # An HTTP status means the origin or the proxy answered, so TLS itself got through.
    print("http=%d rc=0" % e.code)
except Exception as e:
    print("http=000 rc=7 err=%s %s" % (type(e).__name__, str(e)[:140]))
PY
}

# ---------------------------------------------------------------- targets
# Allowed hosts, writable paths and process identity differ per policy file, so they are all
# declared together here.
#   ALLOWED_URLS  : hosts in network_policies (must be reachable)
#   WRITE_ALLOW   : directories in filesystem_policy.read_write (must accept a write and delete)
#   WRITE_DENY    : directories in read_only or absent entirely (Landlock must return EPERM)
#   EXPECT_UID    : process.run_as_user; empty when the policy sets none and the image USER wins
#   PIP_PROBE     : a real pip install, which exercises pypi.org and files.pythonhosted.org together
#   PROBE         : the client used for probing (curl or python); must appear in the policy's binaries
#   CERT_URL      : target for the Python certificate check against a protocol: rest endpoint (empty skips)
#   CURL_DENY_URL : an allowed host that curl must still fail to reach, since curl is not in binaries
#   L7_DENY_URL   : an allowed host on a path absent from rules, which must be refused at layer 7
case "$POLICY" in
  pharmasignal)
    ALLOWED_URLS=(
      "https://api.fda.gov/drug/event.json?limit=1"
      "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?pagesize=1"
      "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json"
      "https://integrate.api.nvidia.com/v1/models"
    )
    WRITE_ALLOW=( /work/out )
    WRITE_DENY=( /etc /sandbox )
    # A process block (1500:1500) was added to the policy on 2026-09-25. An existing sandbox
    # pins its static section, so this expectation only holds after deleting and recreating it.
    EXPECT_UID="1500"
    PIP_PROBE=0
    PROBE=curl
    CERT_URL=""
    CURL_DENY_URL=""
    L7_DENY_URL=""
    ;;
  base)
    ALLOWED_URLS=( "https://integrate.api.nvidia.com/v1/models" )
    WRITE_ALLOW=( /work/out )
    WRITE_DENY=( /etc /sandbox )
    EXPECT_UID="1500"
    PIP_PROBE=0
    PROBE=curl
    CERT_URL=""
    CURL_DENY_URL=""
    L7_DENY_URL=""
    ;;
  flydock)
    # Seven allowed hosts. Each path must be one the policy's rules permit: poking an arbitrary
    # path returns 403 at layer 7, and that would be a bug in this test, not in the policy.
    ALLOWED_URLS=(
      "https://health.api.nvidia.com/v1/biology/mit/diffdock"
      "https://integrate.api.nvidia.com/v1/models"
      "https://files.rcsb.org/download/1CRN.pdb"
      "https://drug.flybrain.kr/data/evidence/summary.json"
      "https://api.fda.gov/drug/event.json?limit=1"
      "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?pagesize=1"
      "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json"
    )
    WRITE_ALLOW=( /work/out )
    WRITE_DENY=( /etc /sandbox /work )
    EXPECT_UID="1500"
    PIP_PROBE=0
    # /usr/bin/curl was removed from the inference and data-source blocks (hardening item 1,
    # taken from the DLI course comparison).
    PROBE=python
    # A DiffDock POST would spend credits, so we go only as far as a GET returning 405.
    CERT_URL="https://health.api.nvidia.com/v1/biology/mit/diffdock"
    # Reuse the URL Python gets 200 from: the refusal must come from the binary, not the host.
    CURL_DENY_URL="https://integrate.api.nvidia.com/v1/models"
    # Allowed host, allowed binary, but a path missing from rules. Layer 7 must answer 403.
    L7_DENY_URL="https://api.fda.gov/drug/label.json?limit=1"
    ;;
esac
BLOCKED_URLS=( "https://example.com/" "https://github.com/" )

PASS=0; FAIL=0
{
  echo "# OpenShell smoke test"
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "policy: $POLICY.yaml   sandbox: $SANDBOX_NAME   via: $WHERE"
  echo "openshell: $(vm 'openshell --version' 2>/dev/null | tr -d '\r')"
  echo
} > "$OUT"

record() {  # record <PASS|FAIL> <이름> <상세>
  printf '%-4s %-34s %s\n' "$1" "$2" "$3" | tee -a "$OUT"
  if [ "$1" = PASS ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
}

probe_url() {  # probe_url <url>  → "http=NNN rc=N" 한 줄
  case "${PROBE:-curl}" in
    python)
      sbpy "$(py_probe_src "$1")" | tr -d '\r' | tr '\n' ' ' | sed 's/  */ /g' ;;
    *)
      sb "curl -sS --max-time 25 -o /dev/null -w \"http=%{http_code}\" \"$1\" 2>&1; echo \" rc=\$?\"" | tr -d '\r' | tr '\n' ' ' | sed 's/  */ /g' ;;
  esac
}

# ---------------------------------------------------------------- 1. allowed hosts
echo "## 허용 도메인 (클라이언트: $PROBE)" | tee -a "$OUT"
for u in "${ALLOWED_URLS[@]}"; do
  host="$(echo "$u" | cut -d/ -f3)"
  out="$(probe_url "$u")"
  # http=000 means the proxy refused CONNECT. 2xx, 401 (no key), 404 (no such path) and 405
  # (GET on a POST-only path) all prove the origin server answered, so they count as allowed.
  # 403 is excluded on purpose: a layer 7 refusal (policy_denied) arrives with exactly that code.
  if echo "$out" | grep -q 'rc=0' && echo "$out" | grep -qE 'http=(2[0-9][0-9]|401|404|405)'; then
    record PASS "allowed $host" "$out"
  else
    record FAIL "allowed $host" "$out (정책 binaries 에 이 클라이언트가 있는지, 호스트와 경로가 endpoints 의 rules 에 있는지 확인)"
  fi
done

# ---------------------------------------------------------------- 2. hosts off the allow list
echo | tee -a "$OUT"; echo "## 허용 목록 밖(차단되어야 함)" | tee -a "$OUT"
for u in "${BLOCKED_URLS[@]}"; do
  host="$(echo "$u" | cut -d/ -f3)"
  out="$(probe_url "$u")"
  # When the proxy answers CONNECT with 403, curl exits 56 and Python raises (rc=7).
  if echo "$out" | grep -qE 'rc=(56|7|35|22)' || echo "$out" | grep -qi 'tunnel failed\|tunnel connection failed\|policy_denied\|http=403'; then
    record PASS "blocked $host" "$out"
  else
    record FAIL "blocked $host" "차단되지 않았다: $out"
  fi
done

# ---------------------------------------------------------------- 2b. binaries left out of the policy
# For the same host, the kernel still sees which executable is connecting. Hit the URL Python
# just got 200 from, this time with curl, and confirm it is refused. This is where hardening
# item 1 either holds or does not.
if [ -n "${CURL_DENY_URL:-}" ]; then
  echo | tee -a "$OUT"; echo "## 허용 호스트, 정책에 없는 바이너리(curl)" | tee -a "$OUT"
  host="$(echo "$CURL_DENY_URL" | cut -d/ -f3)"
  out="$(sb "curl -sS --max-time 25 -o /dev/null -w \"http=%{http_code}\" \"$CURL_DENY_URL\" 2>&1; echo \" rc=\$?\"" | tr -d '\r' | tr '\n' ' ' | sed 's/  */ /g')"
  if echo "$out" | grep -qE 'rc=(56|7|35|22)' || echo "$out" | grep -qi 'tunnel failed\|policy_denied\|http=403'; then
    record PASS "curl denied $host" "$out"
  else
    record FAIL "curl denied $host" "curl 이 허용 호스트에 닿았다(정책 binaries 에 curl 이 남아 있는지 확인): $out"
  fi
fi

# ---------------------------------------------------------------- 2c. paths absent from rules
# This checks what replacing access with rules bought us (hardening item 3). Host and binary are
# both allowed, but with the path missing from rules the proxy cuts the request at layer 7 and
# returns policy_denied.
if [ -n "${L7_DENY_URL:-}" ]; then
  echo | tee -a "$OUT"; echo "## 허용 호스트, rules 에 없는 경로 (L7 거부)" | tee -a "$OUT"
  target="$(echo "$L7_DENY_URL" | cut -d/ -f3-)"
  out="$(probe_url "$L7_DENY_URL")"
  if echo "$out" | grep -qE 'http=403' || echo "$out" | grep -qi 'policy_denied'; then
    record PASS "l7 denied ${target%%\?*}" "$out"
  else
    record FAIL "l7 denied ${target%%\?*}" "경로 제한이 걸리지 않았다: $out"
  fi
fi

# ---------------------------------------------------------------- 3. write restrictions
echo | tee -a "$OUT"; echo "## 파일시스템 쓰기" | tee -a "$OUT"
for d in "${WRITE_DENY[@]}"; do
  out="$(sb "echo probe > $d/openshell_smoke_probe 2>&1; echo \"rc=\$?\"" | tr '\n' ' ' | sed 's/  */ /g')"
  if echo "$out" | grep -qE 'rc=[1-9]'; then
    record PASS "denied write $d" "$out"
  else
    record FAIL "denied write $d" "쓰기가 성공했다(read_write 에 들어갔거나 Landlock 미적용): $out"
    sb "rm -f $d/openshell_smoke_probe" >/dev/null 2>&1
  fi
done

for d in "${WRITE_ALLOW[@]}"; do
  out="$(sb "echo probe > $d/openshell_smoke_probe 2>&1 && cat $d/openshell_smoke_probe && rm -f $d/openshell_smoke_probe; echo \"rc=\$?\"" | tr '\n' ' ' | sed 's/  */ /g')"
  if echo "$out" | grep -q 'rc=0'; then
    record PASS "allowed write $d" "$out"
  else
    record FAIL "allowed write $d" "$out (이미지에 $d 가 있고 소유자가 샌드박스 사용자인지 확인)"
  fi
done

# ---------------------------------------------------------------- 3b. process identity
# When the policy sets process.run_as_user, the uid inside the sandbox must equal it.
echo | tee -a "$OUT"; echo "## 프로세스 신원 (process.run_as_user)" | tee -a "$OUT"
idout="$(sb 'id' | tr '\n' ' ' | sed 's/  */ /g')"
if [ -n "$EXPECT_UID" ]; then
  if echo "$idout" | grep -q "uid=$EXPECT_UID("; then
    record PASS "run_as_user=$EXPECT_UID" "$idout"
  else
    record FAIL "run_as_user=$EXPECT_UID" "기대한 uid 가 아니다: $idout"
  fi
else
  printf '%-4s %-34s %s\n' "INFO" "run_as_user 미지정" "$idout" | tee -a "$OUT"
fi

# ---------------------------------------------------------------- 3c. real pip install (optional)
# A genuine task that needs both pypi.org and files.pythonhosted.org in one go.
# HOME is not writable here, so --no-cache-dir is required rather than merely tidy.
if [ "$PIP_PROBE" = 1 ]; then
  echo | tee -a "$OUT"; echo "## pip 다운로드 (pypi.org + files.pythonhosted.org)" | tee -a "$OUT"
  out="$(sb 'rm -rf /tmp/pipdl; pip3 download --no-deps --no-cache-dir --dest /tmp/pipdl six 2>&1 | tail -3; echo "rc=$?"; ls /tmp/pipdl 2>/dev/null' | tr '\n' ' ' | sed 's/  */ /g')"
  if echo "$out" | grep -q 'six.*\.whl'; then
    record PASS "pip download six" "$out"
  else
    record FAIL "pip download six" "$out"
  fi
  sb 'rm -rf /tmp/pipdl' >/dev/null 2>&1
fi

# ---------------------------------------------------------------- 3d. Python certificate verification
# For a protocol: rest endpoint the proxy terminates TLS, so a client inside the sandbox verifies
# the proxy's certificate rather than the origin's. Every tool we ship is Python, so the pipeline
# can only run inside the sandbox if Python passes this verification.
#
# Measured (2026-09-25): OpenShell installs its CA at /etc/openshell-tls/ca-bundle.pem and points
# SSL_CERT_FILE, REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE, GIT_SSL_CAINFO and NODE_EXTRA_CA_CERTS at it.
# Three separate checks follow from that:
#   default_ctx  : ssl.create_default_context(), which reads SSL_CERT_FILE and therefore works.
#                  Same path our tools take through urllib.request.urlopen, so this one decides
#                  pass or fail.
#   openshell_ca : the bundle given explicitly, confirming the path is valid. Also required.
#   certifi_ca   : the certifi bundle forced in. httpx defaults to it, so failure is expected and
#                  indicates a client configuration choice, not a broken policy. Recorded only.
if [ -n "${CERT_URL:-}" ]; then
  echo | tee -a "$OUT"; echo "## 파이썬 인증서 검증 ($CERT_URL)" | tee -a "$OUT"
  printf '%-4s %-34s %s\n' "INFO" "프록시 환경변수" \
    "$(sb 'printenv | grep -i proxy | sort | tr "\n" " "' | tr '\n' ' ')" | tee -a "$OUT"
  certsrc="$(cat <<PY
import ssl, sys, urllib.request, urllib.error
URL = "$CERT_URL"
def probe(ctx, label):
    req = urllib.request.Request(URL, method="GET",
                                 headers={"User-Agent": "openshell-smoke/1.0"})
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(req, timeout=25) as r:
            print("%s http=%d rc=0" % (label, r.status))
    except urllib.error.HTTPError as e:
        print("%s http=%d rc=0" % (label, e.code))
    except Exception as e:
        print("%s http=000 rc=7 err=%s %s" % (label, type(e).__name__, str(e)[:140]))
import os
print("python=%s %s" % (sys.version.split()[0], ssl.OPENSSL_VERSION))
print("env_SSL_CERT_FILE=%s" % os.environ.get("SSL_CERT_FILE"))
print("env_REQUESTS_CA_BUNDLE=%s" % os.environ.get("REQUESTS_CA_BUNDLE"))
dvp = ssl.get_default_verify_paths()
print("default_verify cafile=%s capath=%s" % (dvp.cafile, dvp.capath))
probe(ssl.create_default_context(), "default_ctx")
bundle = os.environ.get("SSL_CERT_FILE") or "/etc/openshell-tls/ca-bundle.pem"
if os.path.exists(bundle):
    print("openshell_bundle=%s size=%d" % (bundle, os.path.getsize(bundle)))
    probe(ssl.create_default_context(cafile=bundle), "openshell_ca")
else:
    print("openshell_ca absent path=%s" % bundle)
where = None
for mod in ("certifi", "pip._vendor.certifi"):
    try:
        m = __import__(mod, fromlist=["where"])
        w = m.where()
        if w and w != bundle:
            where = w
            print("certifi_from=%s path=%s" % (mod, w))
            break
    except Exception:
        pass
if where:
    probe(ssl.create_default_context(cafile=where), "certifi_ca")
else:
    print("certifi_ca skipped (certifi 번들이 없거나 OpenShell 번들과 같은 경로다)")
PY
)"
  certout="$(sbpy "$certsrc" | tr -d '\r')"
  printf '%s\n' "$certout" | sed 's/^/     /' | tee -a "$OUT"
  # 405 means a GET on a POST-only path, which is itself proof that TLS and routing worked.
  for label in default_ctx openshell_ca; do
    line="$(printf '%s\n' "$certout" | grep "^$label" | head -1)"
    if [ -z "$line" ]; then
      record FAIL "python TLS ($label)" "확인 결과 줄이 없다"
    elif printf '%s' "$line" | grep -qE 'http=(2[0-9][0-9]|401|404|405)'; then
      record PASS "python TLS ($label)" "$line"
    else
      record FAIL "python TLS ($label)" "$line"
    fi
  done
  # The forced-certifi case is not a pass criterion. It stands as a note that a client pinning
  # its own bundle, httpx for instance, needs verify or SSL_CERT_FILE pointed at the OpenShell one.
  certline="$(printf '%s\n' "$certout" | grep '^certifi_ca' | head -1)"
  printf '%-4s %-34s %s\n' "INFO" "python TLS (certifi 강제)" "${certline:-확인 못 함}" | tee -a "$OUT"
fi

# ---------------------------------------------------------------- 4. audit log and effective policy
{
  echo
  echo "## 차단 로그 원문 (openshell logs $SANDBOX_NAME --since 15m, DENIED/BLOCKED 만)"
  vm "openshell logs $SANDBOX_NAME --since 15m -n 500" 2>/dev/null \
    | grep -aiE 'DENIED|BLOCKED' | tail -40 || echo "(로그 없음 또는 logs 명령 실패)"
  echo
  echo "## Landlock 적용 기록 (파일시스템 거부는 커널이 EPERM 으로 끊어 로그에 DENIED 가 남지 않는다)"
  vm "openshell logs $SANDBOX_NAME --since 15m -n 500 --level debug" 2>/dev/null \
    | grep -aE 'Landlock' | tail -4 || echo "(Landlock 로그 없음)"
  echo
  echo "## 유효 정책 (openshell policy get $SANDBOX_NAME --full)"
  vm "openshell policy get $SANDBOX_NAME --full" 2>/dev/null | head -120 || echo "(policy get 실패)"
  echo
  echo "summary: pass=$PASS fail=$FAIL"
} >> "$OUT"

echo
echo "결과 파일: $OUT   (pass=$PASS fail=$FAIL)"
[ "$FAIL" -eq 0 ]
