#!/usr/bin/env bash
# Install Kata Containers from upstream tarball, register as a Docker runtime via
# containerd-shim-kata-v2, and apply performance defaults (templating + memory tuning).
# Idempotent: re-running is safe.
set -euo pipefail

KATA_VERSION="${KATA_VERSION:-3.30.0}"
KATA_PREFIX="${KATA_PREFIX:-/opt/kata}"
TARBALL_URL="https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/kata-static-${KATA_VERSION}-amd64.tar.zst"
TARBALL_LOCAL="/tmp/kata-static-${KATA_VERSION}-amd64.tar.zst"
DOCKER_DAEMON_JSON="/etc/docker/daemon.json"
KATA_CONFIG="${KATA_PREFIX}/share/defaults/kata-containers/configuration.toml"
SHIM_BIN="${KATA_PREFIX}/bin/containerd-shim-kata-v2"

# Performance tuning defaults (override via env to disable)
KATA_DEFAULT_MEMORY="${KATA_DEFAULT_MEMORY:-1024}"   # nominal MiB per VM (virtio-mem hot-plugs more)
KATA_ENABLE_TEMPLATE="${KATA_ENABLE_TEMPLATE:-true}"   # CoW VM forking from a golden template
KATA_ENABLE_VIRTIO_MEM="${KATA_ENABLE_VIRTIO_MEM:-true}"  # return unused VM memory to host
KATA_VM_CACHE_NUMBER="${KATA_VM_CACHE_NUMBER:-8}"    # pre-warmed VM pool size

log() { printf '[install_kata] %s\n' "$*"; }

# 1. Tools we need on host
log "ensuring zstd + curl available"
sudo -n apt-get install -y --no-install-recommends zstd curl >/dev/null 2>&1 || true

# 2. Download tarball (~1.4 GB)
if [ ! -f "$TARBALL_LOCAL" ]; then
    log "downloading $TARBALL_URL ..."
    curl -L --fail --progress-bar -o "$TARBALL_LOCAL" "$TARBALL_URL"
else
    log "tarball already present at $TARBALL_LOCAL ($(du -h "$TARBALL_LOCAL" | awk '{print $1}'))"
fi

# 3. Extract to $KATA_PREFIX
if [ ! -x "$KATA_PREFIX/bin/kata-runtime" ]; then
    log "extracting to $KATA_PREFIX (sudo)"
    sudo -n mkdir -p "$KATA_PREFIX"
    sudo -n tar --use-compress-program=unzstd -xf "$TARBALL_LOCAL" -C / --strip-components=0
    if [ ! -x "$KATA_PREFIX/bin/kata-runtime" ]; then
        log "ERROR: kata-runtime not at $KATA_PREFIX/bin/kata-runtime after extract"
        ls -la "$KATA_PREFIX/bin/" 2>&1 | head -20
        exit 1
    fi
else
    log "kata-runtime already at $KATA_PREFIX/bin/kata-runtime"
fi

# 4. Symlink the containerd shim to PATH (Docker 27+ uses containerd shim model)
log "registering containerd-shim-kata-v2 on PATH"
sudo -n ln -sf "$SHIM_BIN" /usr/local/bin/containerd-shim-kata-v2
sudo -n ln -sf "$KATA_PREFIX/bin/kata-runtime" /usr/local/bin/kata-runtime

log "kata-runtime version: $($KATA_PREFIX/bin/kata-runtime --version 2>&1 | head -1)"

# 5. Add user to kvm group (takes effect on next login)
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx kvm; then
    log "adding $USER to kvm group (effective on next login)"
    sudo -n usermod -aG kvm "$USER"
fi

# 6. Performance defaults: edit Kata config BEFORE registering with Docker
if [ -f "$KATA_CONFIG" ]; then
    log "applying performance defaults to $KATA_CONFIG"
    log "  default_memory     = ${KATA_DEFAULT_MEMORY} MiB"
    log "  enable_virtio_mem  = ${KATA_ENABLE_VIRTIO_MEM}"
    log "  enable_template    = ${KATA_ENABLE_TEMPLATE}"
    log "  vm_cache_number    = ${KATA_VM_CACHE_NUMBER}"
    sudo -n cp "$KATA_CONFIG" "${KATA_CONFIG}.bak.$(date +%Y%m%d-%H%M%S)"
    sudo -n python3 - "$KATA_CONFIG" "$KATA_DEFAULT_MEMORY" "$KATA_ENABLE_VIRTIO_MEM" "$KATA_ENABLE_TEMPLATE" "$KATA_VM_CACHE_NUMBER" <<'PY'
import re, sys
path, mem, virtio, tmpl, cache = sys.argv[1:6]
s = open(path).read()

s = re.sub(r'^default_memory\s*=\s*\d+', f'default_memory = {mem}', s, count=1, flags=re.M)
s = re.sub(r'^enable_virtio_mem\s*=\s*(true|false)', f'enable_virtio_mem = {virtio}', s, count=1, flags=re.M)
s = re.sub(r'^enable_template\s*=\s*(true|false)', f'enable_template = {tmpl}', s, count=1, flags=re.M)

if re.search(r'^vm_cache_number\s*=', s, re.M):
    s = re.sub(r'^vm_cache_number\s*=\s*\d+', f'vm_cache_number = {cache}', s, count=1, flags=re.M)
elif tmpl == 'true':
    s = re.sub(r'(\[factory\][^\[]*?)enable_template\s*=\s*true',
              rf'\1enable_template = true\nvm_cache_number = {cache}',
              s, count=1, flags=re.S)

open(path, 'w').write(s)
print("kata config patched")
PY
else
    log "WARN: $KATA_CONFIG not found; skipping perf defaults"
fi

# 7. Patch /etc/docker/daemon.json — register 'kata' runtime via containerd shim
log "patching $DOCKER_DAEMON_JSON: runtimes.kata → io.containerd.kata.v2"
sudo -n python3 - <<PY
import json, os, shutil, time
path = "$DOCKER_DAEMON_JSON"
d = json.load(open(path)) if os.path.exists(path) else {}
if os.path.exists(path):
    shutil.copy(path, path + ".bak." + time.strftime("%Y%m%d-%H%M%S"))
d.setdefault("runtimes", {})
d["runtimes"]["kata"] = {"runtimeType": "io.containerd.kata.v2"}
json.dump(d, open(path, "w"), indent=4)
print("daemon.json now:", json.dumps(d, indent=2))
PY

# 8. Restart Docker
log "restarting docker daemon (this WILL kill running containers)"
sudo -n systemctl restart docker
sleep 3

# 9. Smoke test
log "smoke: docker run --runtime=kata --rm hello-world"
if ! docker run --runtime=kata --rm hello-world 2>&1 | tee /tmp/kata-smoke.log; then
    log "ERROR: kata smoke test failed; see /tmp/kata-smoke.log"
    log "kata-runtime kata-check output:"
    sudo -n "$KATA_PREFIX/bin/kata-runtime" kata-check 2>&1 | tail -20 || true
    exit 1
fi

log "Kata installed and registered as docker runtime 'kata'."
log "  binary:        $KATA_PREFIX/bin/kata-runtime"
log "  shim:          $SHIM_BIN"
log "  config:        $KATA_CONFIG"
log "  use:           docker run --runtime=kata <image>"
log "  perf defaults: templating=${KATA_ENABLE_TEMPLATE}, mem=${KATA_DEFAULT_MEMORY}MiB, virtio-mem=${KATA_ENABLE_VIRTIO_MEM}, vm_pool=${KATA_VM_CACHE_NUMBER}"
log "  override:      KATA_DEFAULT_MEMORY=2048 KATA_ENABLE_TEMPLATE=false bash $0"
