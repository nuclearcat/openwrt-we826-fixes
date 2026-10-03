#!/usr/bin/env bash
# SPDX-License-Identifier: 0BSD
#
# Reproducible OpenWrt build for the Zbtlink ZBT-WE826 (16M).
#
# Everything compiles inside a Docker container. Nothing is installed on the
# host and no host toolchain is used -- this matters here because the host runs
# uutils coreutils / bfs / ugrep, which OpenWrt's prereq check rejects (it
# insists on GNU coreutils, findutils and grep).
#
# Carries patches/ on top of a pinned upstream tag. The reason this tree exists:
# upstream's mt7620a_zbtlink_zbt-we826.dtsi never wires the mPCIe MT7612E's
# calibration EEPROM, so mt76x2e falls back to the blank on-chip efuse and comes
# up with a random MAC, country 00 and 3 dBm tx power.
#
# Usage:  ./build.sh [command]
#   env      build the Docker builder image
#   src      clone OpenWrt at $OPENWRT_TAG and install $PROFILE overlay
#   config   generate .config from packages.txt
#   build    compile (download + world)
#   verify   check the built DTB carries the fix, diff manifest vs packages.txt
#   image    print the path of the sysupgrade image
#   shell    interactive shell inside the builder container
#   flash    scp the image to $ROUTER and sysupgrade -c (prompts first)
#   clean    remove build_dir/staging_dir (keeps downloads + .config)
#   all      env -> src -> config -> build -> verify   (default)

set -euo pipefail

OPENWRT_TAG="${OPENWRT_TAG:-v24.10.5}"
TARGET="${TARGET:-ramips}"
SUBTARGET="${SUBTARGET:-mt7620}"
DEVICE="${DEVICE:-zbtlink_zbt-we826-16m}"
DOCKER_IMAGE="${DOCKER_IMAGE:-openwrt-builder:24.10}"
ROUTER="${ROUTER:-}"
PROFILE="${PROFILE:-we826}"
JOBS="${JOBS:-$(nproc)}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/openwrt"
PKGLIST="$HERE/packages.txt"
IMG="bin/targets/$TARGET/$SUBTARGET/openwrt-$TARGET-$SUBTARGET-$DEVICE-squashfs-sysupgrade.bin"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31merror: %s\033[0m\n' "$*" >&2; exit 1; }

# Run a command inside the builder container, with the source tree bind-mounted.
in_docker() {
	docker run --rm -i \
		-v "$SRC":/build \
		-e "FORCE_UNSAFE_CONFIGURE=1" \
		"$DOCKER_IMAGE" "$@"
}

cmd_env() {
	say "Building Docker image $DOCKER_IMAGE (uid $(id -u):$(id -g))"
	docker build \
		-f "$HERE/Dockerfile.openwrt-builder" \
		--build-arg UID="$(id -u)" \
		--build-arg GID="$(id -g)" \
		-t "$DOCKER_IMAGE" "$HERE"
}

cmd_src() {
	if [ ! -d "$SRC/.git" ]; then
		say "Cloning OpenWrt $OPENWRT_TAG"
		git clone --depth 1 --branch "$OPENWRT_TAG" \
			https://github.com/openwrt/openwrt.git "$SRC"
	else
		say "Source tree present ($(git -C "$SRC" describe --tags --always))"
	fi

	say "Installing $PROFILE overlay"
	"$HERE/apply.sh" --openwrt "$SRC" --profile "$PROFILE"
	# Preparing patches alone can leave a stale mac80211 module.
	in_docker make package/kernel/mac80211/clean

	say "Updating feeds (this pulls luci/packages/routing/telephony)"
	in_docker ./scripts/feeds update -a
	in_docker ./scripts/feeds install -a
}

cmd_config() {
	[ -f "$PKGLIST" ] || die "missing $PKGLIST (one package name per line)"
	say "Generating .config for $DEVICE"

	# Pass 1: target only, so the build system emits tmp/.config-package.in
	# (the authoritative list of selectable PACKAGE_* symbols).
	cat > "$SRC/.config" <<-EOF
		CONFIG_TARGET_$TARGET=y
		CONFIG_TARGET_${TARGET}_$SUBTARGET=y
		CONFIG_TARGET_${TARGET}_${SUBTARGET}_DEVICE_$DEVICE=y
	EOF
	in_docker make defconfig >/dev/null 2>&1 || die "pass-1 defconfig failed"

	# Pass 2: intersect the wanted package list with what is actually
	# selectable. ABI-versioned libs (libubox20240329, libjson-c5, ...) and
	# prompt-less kmods are not selectable by name; they arrive via
	# dependency resolution instead, so dropping them here is correct.
	cp "$PKGLIST" "$SRC/packages.txt"
	in_docker bash -c '
		set -eu
		grep -hoE "^[[:space:]]*config PACKAGE_[A-Za-z0-9._+-]+" tmp/.config-package.in \
			| sed "s/.*config PACKAGE_//" | sort -u > /tmp/valid.txt
		grep -vE "^\s*(#|$)" packages.txt | sort -u > /tmp/want.txt
		grep -Fxf /tmp/valid.txt /tmp/want.txt > /tmp/sel.txt || true
		grep -Fxvf /tmp/valid.txt /tmp/want.txt > /tmp/nosel.txt || true
		{
			echo "CONFIG_TARGET_'"$TARGET"'=y"
			echo "CONFIG_TARGET_'"${TARGET}_$SUBTARGET"'=y"
			echo "CONFIG_TARGET_'"${TARGET}_${SUBTARGET}"'_DEVICE_'"$DEVICE"'=y"
			sed "s/^/CONFIG_PACKAGE_/; s/$/=y/" /tmp/sel.txt
		} > .config
		echo "  selectable:     $(wc -l < /tmp/sel.txt)"
		echo "  via deps only:  $(wc -l < /tmp/nosel.txt)"
	'
	in_docker make defconfig 2>&1 | grep -vi "^WARNING: Makefile" | tail -3
	rm -f "$SRC/packages.txt"
}

cmd_build() {
	[ -f "$SRC/.config" ] || die "no .config -- run '$0 config' first"
	say "Building with -j$JOBS (this takes a while on a cold tree)"
	in_docker make -j"$JOBS" download
	in_docker make -j"$JOBS" world
}

cmd_verify() {
	say "Verifying built image"

	local dtb
	dtb="$(in_docker bash -c "find build_dir -name 'image-*${DEVICE#*_}.dtb' | head -1" | tr -d '\r')"
	[ -n "$dtb" ] || die "no built DTB found for $DEVICE"

	echo "-- DTB: $dtb"
	in_docker bash -c "
		set -eu
		dtc -I dtb -O dts '$dtb' 2>/dev/null > /tmp/o.dts
		rc=0

		# 5 GHz: MT7612E calibration EEPROM must come from factory@0x8000,
		# otherwise mt76x2e falls back to the blank efuse (3 dBm, random MAC).
		if grep -q 'wifi@0,0' /tmp/o.dts && grep -q 'eeprom@8000' /tmp/o.dts; then
			echo '   OK  : MT7612E eeprom cell wired into pcie0'
		else
			echo '   FAIL: MT7612E eeprom fix missing'; rc=1
		fi

		# Preserve the pin configuration used by the tested patch stack.
		# This alone gave no measured signal improvement on our unit.
		if grep -q 'pa_gpio' /tmp/o.dts && grep -q 'function = \"pa\"' /tmp/o.dts; then
			echo '   OK  : wmac pa/pa_gpio pinctrl states present'
		else
			echo '   FAIL: external PA/LNA pinctrl states missing'; rc=1
		fi

		# The 'pa' group must NOT be claimed by the global gpio mux, or the
		# pinctrl states above can never take effect.
		if grep -q 'groups = \"i2c.*spi refclk\";' /tmp/o.dts && \
		   ! grep -q 'spi refclk\\\\0pa' /tmp/o.dts; then
			echo '   OK  : pa released from global gpio mux'
		else
			echo '   FAIL: pa still muxed to gpio globally'; rc=1
		fi

		# The wifi LED is on pin 72 (wled), not a pa pin -- it must survive.
		grep -q 'green:wifi' /tmp/o.dts \
			&& echo '   OK  : green:wifi LED preserved' \
			|| { echo '   FAIL: wifi LED lost'; rc=1; }

		exit \$rc
	"

	# Staleness guard. `make world` will happily ship a module compiled before
	# a driver patch was added: prepare re-patches the source but leaves the
	# .built stamp alone, so nothing recompiles. Compare build artefact mtimes
	# against the patches that are supposed to be in them.
	echo "-- Driver patches vs built modules"
	in_docker bash -c '
		newest_patch=$(find package/kernel/mac80211/patches -name "*.patch" -printf "%T@ %p\n" 2>/dev/null \
			| sort -rn | head -1)
		ipk=$(find bin -name "kmod-rt2800-lib*" -printf "%T@ %p\n" 2>/dev/null | sort -rn | head -1)
		[ -n "$ipk" ] || { echo "   WARN: kmod-rt2800-lib not built"; exit 0; }
		pt=${newest_patch%% *}; it=${ipk%% *}
		if [ "${it%.*}" -ge "${pt%.*}" ]; then
			echo "   OK  : kmod-rt2800-lib newer than newest mac80211 patch"
		else
			echo "   FAIL: kmod-rt2800-lib is STALE (built before ${newest_patch#* })"
			echo "         run: make package/kernel/mac80211/clean, then rebuild"
			exit 1
		fi
	'

	local man="bin/targets/$TARGET/$SUBTARGET/openwrt-$TARGET-$SUBTARGET-$DEVICE.manifest"
	[ -f "$SRC/$man" ] || { echo "-- no manifest yet"; return 0; }
	echo "-- Package manifest vs $(basename "$PKGLIST")"
	cp "$PKGLIST" "$SRC/packages.txt"
	in_docker bash -c "
		export LC_ALL=C
		awk '{print \$1}' '$man' | sort -u > /tmp/built.txt
		grep -vE '^\s*(#|$)' packages.txt | sort -u > /tmp/want.txt
		echo '   wanted but absent :' \$(comm -23 /tmp/want.txt /tmp/built.txt | tr '\n' ' ')
		echo '   extra in image    :' \$(comm -13 /tmp/want.txt /tmp/built.txt | tr '\n' ' ')
	"
	rm -f "$SRC/packages.txt"
}

cmd_image() {
	[ -f "$SRC/$IMG" ] || die "image not built yet"
	ls -la "$SRC/$IMG"
	echo "$SRC/$IMG"
}

cmd_shell() { docker run --rm -it -v "$SRC":/build "$DOCKER_IMAGE" bash; }

cmd_clean() {
	say "Removing build_dir/ staging_dir/ (downloads and .config kept)"
	rm -rf "$SRC/build_dir" "$SRC/staging_dir" "$SRC/tmp"
}

cmd_flash() {
	[ -n "$ROUTER" ] || die "set ROUTER=user@router-address explicitly before flashing"
	[ -f "$SRC/$IMG" ] || die "image not built yet"
	local size; size=$(stat -c%s "$SRC/$IMG")
	say "About to flash $ROUTER"
	echo "  image : $SRC/$IMG"
	echo "  size  : $size bytes"
	echo
	echo "  sysupgrade -c preserves /etc/config. A bad image on a network-only"
	echo "  router means TFTP recovery over serial. Keep the stock 24.10.5"
	echo "  image handy."
	echo
	read -r -p "  Type FLASH to continue: " ans
	[ "$ans" = "FLASH" ] || die "aborted"

	scp -O "$SRC/$IMG" "$ROUTER:/tmp/sysupgrade.bin"
	# sysupgrade kills the ssh session as it reboots; that is expected.
	ssh "$ROUTER" 'sysupgrade -c /tmp/sysupgrade.bin' || true
	say "Flashing started -- the router will reboot. Give it ~2 minutes."
}

cmd_all() { cmd_env; cmd_src; cmd_config; cmd_build; cmd_verify; cmd_image; }

case "${1:-all}" in
	env|src|config|build|verify|image|shell|clean|flash|all) "cmd_${1:-all}" ;;
	*) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
