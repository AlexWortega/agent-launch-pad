#!/usr/bin/env bash
# Install Kata Containers 3.30.0 from upstream tarball, register as Docker runtime.
# Idempotent: re-running is safe.
set -euo pipefail

KATA_VERSION="${KATA_VERSION:-3.30.0}"
KATA_PREFIX="${KATA_PREFIX:-/opt/kata}"
TARBALL_URL="https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/kata-static-${KATA_VERSION}-amd64.tar.zst"
TARBALL_LOCAL="/tmp/kata-static-${KATA_VERSION}-amd64.tar.zst"
DOCKER_DAEMON_JSON="/etc/docker/daemon.json"

log() { printf '[install_kata] %s\n' "$*"; }

# 1. Tools we need on host
log "ensuring zstd + curl available"
sudo -n apt-get install -y --no-install-recommends zstd curl >/dev/null 2>&1 || true

# 2. Download tarball (1.4GB)
if [ ! -f "$TARBALL_LOCAL" ]; then
    log "downloading $TARBALL_URL ..."
    curl -L --fail --progress-bar -o "$TARBALL_LOCAL" "$TARBALL_URL"
else
    log "tarball already present at $TARBALL_LOCAL ($(du -h "$TARBALL_LOCAL" | awk '{print $1}'))"
fi

# 3. Extract under $KATA_PREFIX (the tarball expects /opt/kata as root)
if [ ! -x "$KATA_PREFIX/bin/kata-runtime" ]; then
    log "extracting to $KATA_PREFIX (sudo)"
    sudo -n mkdir -p "$KATA_PREFIX"
    # tarball internally has paths like ./opt/kata/... so strip-components=2 lands files in $KATA_PREFIX
    sudo -n tar --use-compress-program=unzstd -xf "$TARBALL_LOCAL" -C / --strip-components=0
    if [ ! -x "$KATA_PREFIX/bin/kata-runtime" ]; then
        log "ERROR: kata-runtime not at $KATA_PREFIX/bin/kata-runtime after extract"
        ls -la "$KATA_PREFIX/bin/" 2>&1 | head -20
        exit 1
    fi
else
    log "kata-runtime already at $KATA_PREFIX/bin/kata-runtime"
fi

# 4. Convenience symlink so PATH/programs find it
sudo -n ln -sf "$KATA_PREFIX/bin/kata-runtime" /usr/local/bin/kata-runtime

log "kata-runtime version: $($KATA_PREFIX/bin/kata-runtime --version 2>&1 | head -1)"

# 5. Add user to kvm group (takes effect on next login; sudo path used now)
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx kvm; then
    log "adding $USER to kvm group (effective on next login)"
    sudo -n usermod -aG kvm "$USER"
fi

# 6. Patch /etc/docker/daemon.json — add kata runtime, preserve existing entries
log "patching $DOCKER_DAEMON_JSON to register 'kata' runtime"
sudo -n python3 - <<PY
import json, os, shutil, time
path = "$DOCKER_DAEMON_JSON"
if os.path.exists(path):
    with open(path) as f:
        d = json.load(f)
else:
    d = {}
shutil.copy(path, path + ".bak." + time.strftime("%Y%m%d-%H%M%S")) if os.path.exists(path) else None
d.setdefault("runtimes", {})
d["runtimes"]["kata"] = {"path": "$KATA_PREFIX/bin/kata-runtime"}
with open(path, "w") as f:
    json.dump(d, f, indent=4)
print("daemon.json now:", json.dumps(d, indent=2))
PY

# 7. Restart docker daemon
log "restarting docker daemon (this WILL kill running containers)"
sudo -n systemctl restart docker
sleep 3

# 8. Smoke test
log "smoke: docker run --runtime=kata --rm hello-world"
if ! docker run --runtime=kata --rm hello-world 2>&1 | tee /tmp/kata-smoke.log; then
    log "ERROR: kata smoke test failed; see /tmp/kata-smoke.log"
    log "kata-runtime kata-check output:"
    sudo -n "$KATA_PREFIX/bin/kata-runtime" kata-check 2>&1 | tail -20 || true
    exit 1
fi

log "Kata installed and registered as docker runtime 'kata'."
log "  binary:    $KATA_PREFIX/bin/kata-runtime"
log "  use:       docker run --runtime=kata <image>"
