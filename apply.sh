#!/usr/bin/env bash
# SPDX-License-Identifier: 0BSD
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/scripts/apply.py" "$@"
