#!/usr/bin/env python3
# SPDX-License-Identifier: 0BSD
"""Install the WE826 patch overlay without touching unrelated OpenWrt files."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
SUPPORTED_COMMIT = '4f7e6e554be2aef6a55be36f9f954d56705eb2ee'
SUPPORTED_TAG = 'v24.10.5'
DTS = 'target/linux/ramips/dts/mt7620a_zbtlink_zbt-we826.dtsi'
DRIVERS = 'package/kernel/mac80211/patches/rt2x00/'
STATE = '.we826-fixes.json'
PROFILES = ('we826', 'we826-experimental')


def run(*args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check)


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def read(path):
    return path.read_bytes() if path.exists() else None


def encode(data):
    return base64.b64encode(data).decode() if data is not None else None


def decode(data):
    return base64.b64decode(data, validate=True) if data is not None else None


def safe(root, name):
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError(f'Refusing symlink or path outside checkout: {name}')
    return path


def write(path, data):
    if data is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def prepare(root, profile, unapply):
    head = run('git', 'rev-parse', 'HEAD', cwd=root).stdout.strip()
    if head != SUPPORTED_COMMIT:
        raise ValueError(f'Only OpenWrt {SUPPORTED_TAG} ({SUPPORTED_COMMIT[:12]}) is supported; got {head[:12]}')
    driver_files = sorted((REPO / 'mac80211-patches').glob('*.patch'))
    names = [DTS] + [DRIVERS + p.name for p in driver_files]
    current = {name: read(safe(root, name)) for name in names}
    state_path = safe(root, STATE)
    state_bytes = read(state_path)
    if state_bytes:
        state = json.loads(state_bytes)
        if state.get('format') != 1 or set(state['original']) != set(names) or set(state['installed']) != set(names):
            raise ValueError('Installer state is incompatible with this release. Unapply using the release that installed it.')
        for name, data in current.items():
            if digest(data) != state['installed'][name]:
                raise ValueError(f'Locally modified managed file: {name}. Preserve/revert your edit before switching or removing the overlay.')
        original = {name: decode(value) for name, value in state['original'].items()}
    else:
        if unapply:
            raise ValueError('No installation state found; nothing will be removed.')
        original = dict(current)
        for p in driver_files:
            if current[DRIVERS + p.name] not in (None, p.read_bytes()):
                raise ValueError(f'Conflicting existing patch: {p.name}. Use a fresh checkout or resolve it first.')
    if original[DTS] is None:
        raise ValueError(f'Missing board file: {DTS}')
    desired = dict(original)
    if not unapply:
        with tempfile.TemporaryDirectory(prefix='we826-check-') as temp:
            # git apply outside a repository works on these scratch files only.
            tree = Path(temp)
            write(tree / DTS, original[DTS])
            for patch in sorted((REPO / 'patches').glob('*.patch')):
                reverse = run('git', 'apply', '--check', '--reverse', str(patch), cwd=tree, check=False)
                if reverse.returncode == 0:
                    continue
                result = run('git', 'apply', '--check', str(patch), cwd=tree, check=False)
                if result.returncode:
                    raise ValueError(f'Device-tree preflight failed for {patch.name}:\n{result.stderr.strip()}')
                run('git', 'apply', str(patch), cwd=tree)
            desired[DTS] = (tree / DTS).read_bytes()
        for p in driver_files:
            selected = profile == 'we826-experimental' or p.name.startswith(('998-', '999-'))
            desired[DRIVERS + p.name] = p.read_bytes() if selected else original[DRIVERS + p.name]
        # Never silently leave experimental patches active in the basic profile.
        if profile == 'we826' and any(desired[DRIVERS + p.name] is not None
                                     for p in driver_files if p.name.startswith(('999a-', '999b-', '999c-'))):
            raise ValueError('Experimental patches predate this installation; use a fresh checkout for the we826 profile.')
    next_state = None if unapply else (json.dumps({
        'format': 1, 'profile': profile, 'openwrt_commit': head,
        'original': {name: encode(data) for name, data in original.items()},
        'installed': {name: digest(data) for name, data in desired.items()},
    }, indent=2, sort_keys=True) + '\n').encode()
    current[STATE] = state_bytes
    desired[STATE] = next_state
    return current, desired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openwrt', required=True, type=Path, help='OpenWrt v24.10.5 git checkout')
    parser.add_argument('--profile', choices=PROFILES, default='we826')
    parser.add_argument('--check', action='store_true', help='preflight only; do not change the checkout')
    parser.add_argument('--unapply', action='store_true', help='restore files to their pre-installation contents')
    args = parser.parse_args()
    root = args.openwrt.resolve(strict=True)
    lock = safe(root, '.we826-fixes.lock')
    locked = False
    try:
        if not args.check:
            try:
                with lock.open('x'):
                    pass
                locked = True
            except FileExistsError:
                raise ValueError('Another installer may be running (.we826-fixes.lock exists).')
        before, after = prepare(root, args.profile, args.unapply)
        changed = [name for name in after if before[name] != after[name]]
        for name in changed:
            if name != STATE:
                print(('remove ' if after[name] is None else 'update ') + name)
        if args.check:
            print('Preflight passed. No checkout files changed. Driver compilation/patch application is checked by CI and the OpenWrt build.')
            return
        # All validation is complete before the first target file is changed.
        applied = []
        try:
            for name in changed:
                applied.append(name)
                write(safe(root, name), after[name])
        except OSError:
            for name in reversed(applied):
                write(safe(root, name), before[name])
            raise
        print('Overlay removed.' if args.unapply else f'Overlay ready: {args.profile} ({SUPPORTED_TAG}).')
        print('Before building firmware, run in OpenWrt:\n  make package/kernel/mac80211/clean\n  make -j"$(nproc)"')
    finally:
        if locked:
            lock.unlink()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f'error: {exc}')
