#!/usr/bin/env bash
# Apply Kata performance tweaks: templating + memory tuning + virtio-mem balloon.
# Run AFTER current grid finishes (it does `systemctl restart docker` which kills active runs).
set -euo pipefail

CFG=/opt/kata/share/defaults/kata-containers/configuration.toml
BAK=$CFG.bak.$(date +%Y%m%d-%H%M%S)

log() { printf '[optimize_kata] %s\n' "$*"; }

if [ ! -f "$CFG" ]; then
    log "ERROR: $CFG not found — Kata not installed?"
    exit 1
fi

log "backing up $CFG -> $BAK"
sudo -n cp "$CFG" "$BAK"

log "applying patches:"
log "  default_memory     2048 -> 1024 MB"
log "  enable_virtio_mem  false -> true"
log "  enable_template    false -> true"
log "  vm_cache_number    0    -> 8 (template VM pool)"

sudo -n python3 - "$CFG" <<'PY'
import re, sys
path = sys.argv[1]
with open(path) as f:
    s = f.read()

s = re.sub(r'^default_memory\s*=\s*\d+', 'default_memory = 1024', s, count=1, flags=re.M)
s = re.sub(r'^enable_virtio_mem\s*=\s*false', 'enable_virtio_mem = true', s, count=1, flags=re.M)
s = re.sub(r'^enable_template\s*=\s*false', 'enable_template = true', s, count=1, flags=re.M)

if re.search(r'^vm_cache_number\s*=', s, re.M):
    s = re.sub(r'^vm_cache_number\s*=\s*\d+', 'vm_cache_number = 8', s, count=1, flags=re.M)
else:
    s = re.sub(r'(\[factory\][^\[]*?)enable_template\s*=\s*true',
              r'\1enable_template = true\nvm_cache_number = 8',
              s, count=1, flags=re.S)

# Templating requires initrd (not rootfs image). Switch image=... -> initrd=... + comment original.
if re.search(r'^image\s*=', s, re.M) and not re.search(r'^initrd\s*=', s, re.M):
    initrd_path = "/opt/kata/share/kata-containers/kata-containers-initrd.img"
    s = re.sub(r'^image\s*=', '# image =', s, count=1, flags=re.M)
    s = re.sub(r'^# image =',
              f'initrd = "{initrd_path}"\n# image =', s, count=1, flags=re.M)

with open(path, 'w') as f:
    f.write(s)
print("patches applied")
PY

log "verifying changes..."
grep -E '^(default_memory|enable_virtio_mem|enable_template|vm_cache_number)\s*=' "$CFG" || true

log "restarting docker (this kills running containers)"
read -p "continue? (y/N) " yn
[ "$yn" = "y" ] || { log "aborted; restore: sudo cp $BAK $CFG"; exit 0; }

sudo -n systemctl restart docker
sleep 3

log "smoke test: docker run --runtime=kata --rm hello-world"
docker run --rm --runtime=kata hello-world | tail -3

log "Kata optimized. Boot should now be ~100ms, mem ~1GB nominal per VM."
log "Verify: time docker run --rm --runtime=kata --entrypoint sh agentbench/base-tools:latest -c 'free -m | head -2'"
