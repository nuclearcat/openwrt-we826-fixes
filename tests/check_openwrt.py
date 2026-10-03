#!/usr/bin/env python3
# SPDX-License-Identifier: 0BSD
"""Apply the pinned OpenWrt mac80211 patch series, including the overlay."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

VERSION = '6.12.61'
SHA256 = '9db2f836dba7f38ad68f8798720ad4360bce6a3557bde02b88b3a4f068c77118'
COMMIT = '4f7e6e554be2aef6a55be36f9f954d56705eb2ee'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--openwrt', required=True, type=Path)
parser.add_argument('--archive', type=Path, help='Use a local backports archive instead of downloading')
args = parser.parse_args()
root = args.openwrt.resolve()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
assert head == COMMIT, 'This validation targets OpenWrt v24.10.5 only'
package = root / 'package/kernel/mac80211'
makefile = (package / 'Makefile').read_text()
assert f'PKG_VERSION:={VERSION}' in makefile and f'PKG_HASH:={SHA256}' in makefile
# Keep exactly the ordering used by the pinned package's Build/Patch recipe.
groups = re.findall(r'\$\(call PatchDir,\$\(PKG_BUILD_DIR\),\$\(PATCH_DIR\)/(\w+),', makefile)
assert groups and 'rt2x00' in groups
with tempfile.TemporaryDirectory(prefix='check-openwrt-') as tmp:
    tmp = Path(tmp)
    archive = args.archive or root / 'dl' / f'backports-{VERSION}.tar.xz'
    if not archive.exists():
        archive = tmp / f'backports-{VERSION}.tar.xz'
        url = f'https://sources.openwrt.org/{archive.name}'
        print(f'Downloading {url}', flush=True)
        urllib.request.urlretrieve(url, archive)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == SHA256, 'Backports archive checksum mismatch'
    subprocess.run(['tar', '-xJf', str(archive.resolve()), '-C', str(tmp)], check=True)
    source = tmp / f'backports-{VERSION}'
    total = 0
    for group in groups:
        for patch in sorted((package / 'patches' / group).glob('*.patch')):
            local = group == 'rt2x00' and patch.name.startswith(('998-', '999-', '999a-', '999b-', '999c-'))
            command = ['patch', '--batch', '--forward', '-p1', '-i', str(patch)]
            if local:
                command.append('--fuzz=0')
            result = subprocess.run(command, cwd=source, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if result.returncode:
                raise SystemExit(f'FAILED {group}/{patch.name}\n{result.stdout}')
            total += 1
    print(f'PASS: {total} mac80211 patches applied; local overlay applied without fuzz.')
