#!/usr/bin/env bash
# Explicit user-local installation for Ubuntu Noble's missing LV2 helper.
set -euo pipefail
version=$(pw-cli --version | tail -n 1 | awk '{print $NF}')
if [[ "$version" != 1.0.5 ]]; then
    echo 'This installer requires PipeWire 1.0.5; use a matching distribution host package.' >&2
    exit 1
fi
system_modules="/usr/lib/$(dpkg-architecture -qDEB_HOST_MULTIARCH)/pipewire-0.3"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
curl -fL https://github.com/PipeWire/pipewire/archive/refs/tags/1.0.5.tar.gz -o source.tar.gz
echo 'c5a5de26d684a1a84060ad7b6131654fb2835e03fccad85059be92f8e3ffe993  source.tar.gz' | sha256sum -c -
tar -xzf source.tar.gz
apt download liblilv-dev libserd-dev libsord-dev libsratom-dev libzix-dev lv2-dev libpipewire-0.3-dev libspa-0.2-dev
for package in ./*.deb; do dpkg-deb -x "$package" headers; done
cc -shared -fPIC -O2 -Iheaders/usr/include/pipewire-0.3 -Iheaders/usr/include/spa-0.2 \
    -Iheaders/usr/include/lilv-0 -Iheaders/usr/include \
    -Ipipewire-1.0.5/src/modules/module-filter-chain \
    pipewire-1.0.5/src/modules/module-filter-chain/lv2_plugin.c \
    -Wl,-l:liblilv-0.so.0 -Wl,-l:libpipewire-0.3.so.0 -lm \
    -o libpipewire-module-filter-chain-lv2.so
install_dir="$HOME/.local/lib/eqspace/pipewire-1.0.5"
mkdir -p "$install_dir"
for module in "$system_modules"/*.so; do ln -sfn "$module" "$install_dir/"; done
install -m 755 libpipewire-module-filter-chain-lv2.so "$install_dir/"
echo "Installed matching LV2 host in $install_dir. Restart EQ-Space."
