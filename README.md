# OpenWrt fixes for ZBT-WE826

A small patch overlay for **OpenWrt 24.10.5**, tested on a **ZBT-WE826 (16M),
MT7620A revision 2 / ECO 6**, with an MT7612E PCIe radio.

The patches restore the 5 GHz radio's factory calibration and correct the
2.4 GHz amplifier configuration on the tested board. An optional experimental
profile adds internal-PA digital predistortion (DPD) and temperature tracking.

Developed through analysis of hardware register behavior and testing on
ZBT-WE826 hardware. Experimental software, supplied without warranty.

## Apply to an OpenWrt checkout

You need Git, Python 3.9 or newer, Bash, and the normal OpenWrt build tools.
Use a dedicated OpenWrt checkout for this board:

```sh
git clone https://github.com/nuclearcat/openwrt-we826-fixes.git
git clone --branch v24.10.5 --depth 1 https://github.com/openwrt/openwrt.git

./openwrt-we826-fixes/apply.sh --openwrt ./openwrt --check
./openwrt-we826-fixes/apply.sh --openwrt ./openwrt --profile we826
```

The installer applies the device-tree changes, copies driver patches into
`package/kernel/mac80211/patches/rt2x00/`, and saves rollback information in
`.we826-fixes.json` inside the OpenWrt checkout. Repeating the command is safe.
It rejects other OpenWrt commits and conflicting edits rather than forcing
patches onto unknown source. `--check` preflights the board changes and file
conflicts; the actual driver patch application is verified during preparation
and compilation, and in CI.

Then configure and build OpenWrt:

```sh
cd openwrt
./scripts/feeds update -a
./scripts/feeds install -a
make menuconfig
# Target: MediaTek Ralink MIPS / MT7620 / Zbtlink ZBT-WE826 (16M)
# Include kmod-rt2800-soc; for the MT7612E card, include kmod-mt76x2.
make package/kernel/mac80211/clean
make -j"$(nproc)"
```

**Rebuild the firmware image.** The device-tree change is part of the kernel;
installing a Wi-Fi package alone is insufficient. Cleaning mac80211 is also
required after changing profiles so a previously compiled driver is not reused.
The installer does not flash a router or change its configuration.

## Profiles

| Profile | Included changes |
| --- | --- |
| `we826` (default) | 0001 calibration EEPROM; 0002 PA pinctrl; 998 internal PA selection; 999 internal LNA selection |
| `we826-experimental` | All of the above, plus 999a DPD, 999b temperature tracking, and 999c DPD table shaping |

```sh
./openwrt-we826-fixes/apply.sh --openwrt ./openwrt --profile we826-experimental
```

Switch back by applying `--profile we826` and cleaning/rebuilding mac80211.
Switching removes experimental patches installed by this tool. Pre-existing
files are preserved; use a fresh checkout if experimental patches were already
present before the first installation.

0002 is retained to reproduce the tested pin configuration. It gave no measured
signal improvement on this unit; removing it still needs a separate hardware test.

**The 2.4 GHz patches change shared RT6352 driver behavior.** They are workarounds
for this board's EEPROM, not a general fix for every MT7620 device. Do not use
this patched build tree to make images for other boards with external amplifiers.
See [the driver TODO](TODO_rt2x00.md) for the work needed to narrow their scope.

## Remove

```sh
./openwrt-we826-fixes/apply.sh --openwrt ./openwrt --unapply --check
./openwrt-we826-fixes/apply.sh --openwrt ./openwrt --unapply
```

This restores the managed files to their contents before the first installation.
It refuses to overwrite later edits to those files. Keep `.we826-fixes.json`
until removal is complete. Other OpenWrt files and settings are not managed.
Clean mac80211 and rebuild afterward.

## Docker build helper

For a GNU build environment in Docker:

```sh
cd openwrt-we826-fixes
./build.sh
# Or: PROFILE=we826-experimental ./build.sh
```

This clones OpenWrt under `openwrt/`, installs the selected profile, and builds
a WE826 image. `packages.txt` is the example package selection used by the helper;
adapt it for your installation. An existing manually patched checkout may need
to be replaced with a fresh checkout to use profile switching and rollback.

## Results and validation

Recorded on one unit; these are observations, not performance guarantees:

- 0001 restored the factory MAC and increased reported 5 GHz transmit power
  from 3 to 14 dBm on channel 36. A client negotiated 433.3 Mbit/s.
- 998 increased the phone's received 2.4 GHz signal from -66 to -38 dBm,
  while control AP readings changed by at most 1 dB. This is not a calibrated
  transmitter-power measurement.
- Adding 999 to an otherwise identical 998 + DPD setup improved reported
  client RSSI and measured throughput. The RSSI change alone does not quantify
  receive sensitivity.
- DPD reduced recorded retries from 18.5% to 10.7% in one comparison without
  improving download throughput in that comparison.
- Temperature tracking and table shaping were originally tested together;
  their individual performance contributions remain unproven.

The current DPD failure-handling and temperature-state corrections pass simulated
register tests and compile for the MIPS target. **Those latest corrections have
not yet been flashed and validated on hardware.** No firmware binaries are
provided in this initial repository.

```sh
python3 tests/test_dpd.py
python3 tests/test_temperature.py
python3 tests/test_apply.py --openwrt ./openwrt
# Full patch preparation check (downloads the pinned backports archive if needed):
python3 tests/check_openwrt.py --openwrt ./openwrt
```

The GitHub workflow checks both profiles against the pinned OpenWrt checkout,
including the upstream mac80211 patch series. It does not perform RF testing
or a full firmware build.

## Licensing

Original build/installation tools and tests use [0BSD](LICENSES/0BSD.txt).
Driver patches follow the GPL-2.0-or-later licensing of the modified driver;
device-tree patches follow the applicable OpenWrt licensing. Existing notices
and third-party rights remain in effect. See [LICENSE.md](LICENSE.md).
