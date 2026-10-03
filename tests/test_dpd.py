#!/usr/bin/env python3
# SPDX-License-Identifier: 0BSD
"""Compile the actual 999a DPD code against a small register simulator."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
patch = next((root / 'mac80211-patches').glob('999a-*.patch')).read_text()
added = '\n'.join(line[1:] for line in patch.splitlines()
                  if line.startswith('+') and not line.startswith('+++'))
start = added.index('static const u8 rt2800_dpd_vga_gain[]')
# The second hunk contains the caller's indented DPD block.
end = added.index('\n\t/*\n\t * DPD calibration', start)
functions = added[start:end]
caller = added[end:]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define MAC_STATUS_CFG 0
#define MAC_SYS_CTRL 1
#define TX_PIN_CFG 2
#define TX_ALC_CFG_0 3
#define TX_ALC_CFG_0_CH_INIT_0 0xff
#define rt2x00_warn(...) ((void)0)
#define rt2x00_dbg(...) ((void)0)
#define udelay(n) ((void)0)
struct rt2x00_dev { struct { u8 tx_chain_num; } default_ant; };
static u8 bbp[256], rf[8][64], saved_rf[8][64];
static u32 mac[4], saved_mac[4];
static unsigned captures, status_reads, clear_mask, sample_reads;
static int scenario, chain;
static bool external_pa;
static unsigned rt2800_hw_get_chipver(struct rt2x00_dev *d) { return 2; }
static bool rt2x00_has_cap_external_pa(struct rt2x00_dev *d) { return external_pa; }
static unsigned rt2x00_get_field32(unsigned v, unsigned mask) { return v & mask; }
static u32 rt2800_register_read(struct rt2x00_dev *d, unsigned r) {
    if (r == MAC_STATUS_CFG) {
        ++status_reads;
        if (scenario >= 2 && scenario <= 4 && status_reads >= (unsigned)(scenario - 1))
            return 3;
        return 0;
    }
    return mac[r];
}
static void rt2800_register_write(struct rt2x00_dev *d, unsigned r, u32 v) { mac[r] = v; }
static u8 rt2800_rfcsr_read_bank(struct rt2x00_dev *d, unsigned b, unsigned r) { return rf[b][r]; }
static void rt2800_rfcsr_write_bank(struct rt2x00_dev *d, unsigned b, unsigned r, u8 v) {
    rf[b][r] = v;
    if (b == 0 && r == 1 && (v == 0x41 || v == 0x42)) chain = v - 0x41;
}
static void rt2800_bbp_write(struct rt2x00_dev *d, unsigned r, u8 v) {
    bbp[r] = v;
    if (r == 188 && bbp[186] == 0 && bbp[187] == 1 && (v & 128)) {
        ++captures;
        assert(captures <= 200); /* detects an unbounded oscillating search */
    }
    if (r == 189 && v == 0x0f) clear_mask |= 1u << bbp[186];
}
static u8 rt2800_bbp_read(struct rt2x00_dev *d, unsigned r) {
    if (r != 188) return bbp[r];
    if (bbp[186] == 0 && bbp[187] == 1) return scenario == 5 ? 128 : 0;
    if (bbp[186] == 1) {
        ++sample_reads;
        if (bbp[187] == 255) {
            if (scenario == 1) return (rf[chain ? 7 : 5][3] & 31) <= 0x0c ? 100 : 250;
            return 200;
        }
        if (scenario == 6 || (scenario == 7 && chain == 0) || (scenario == 8 && chain == 1))
            return 0xff; /* invalid linearity */
        return bbp[187] == 0x2b ? 20 : 9;
    }
    return bbp[r];
}
'''
main = r'''
static void reset(int mode) {
    scenario = mode; captures = status_reads = clear_mask = sample_reads = 0;
    external_pa = false;
    memset(bbp, 0, sizeof(bbp)); memset(rf, 0x17, sizeof(rf));
    mac[0] = 0; mac[1] = 12; mac[2] = 0x123456; mac[3] = 34;
    bbp[4] = 0x18; bbp[27] = 7; bbp[65] = 8; bbp[159] = 9;
    bbp[241] = 10; bbp[242] = 11; bbp[244] = 0;
    memcpy(saved_rf, rf, sizeof(rf)); memcpy(saved_mac, mac, sizeof(mac));
}
static void restored(void) {
    assert(!memcmp(saved_rf, rf, sizeof(rf)));
    assert(!memcmp(saved_mac, mac, sizeof(mac)));
    assert(bbp[4] == 0x18 && bbp[27] == 7 && bbp[65] == 8 && bbp[159] == 9);
    assert(bbp[241] == 10 && bbp[242] == 11 && bbp[244] == 0);
}
int main(void) {
    struct rt2x00_dev dev = { .default_ant.tx_chain_num = 2 };
    for (int mode = 0; mode <= 6; ++mode) {
        reset(mode);
        assert(rt2800_dpd_calibrate_chain(&dev, 0) == (mode == 0));
        restored();
        if (mode == 1) assert(captures == ARRAY_SIZE(rt2800_dpd_vga_gain));
        if (mode >= 2 && mode <= 5) assert(sample_reads == 0);
    }
    for (int mode = 6; mode <= 8; ++mode) {
        reset(mode); run_dpd(&dev); restored();
        assert(clear_mask == (mode == 6 ? ((1u << 2) | (1u << 4)) :
                              mode == 7 ? (1u << 2) : (1u << 4)));
    }
    reset(6); dev.default_ant.tx_chain_num = 1; run_dpd(&dev);
    assert(clear_mask == (1u << 2)); restored();
    reset(0); run_dpd(&dev); assert(clear_mask == 0); restored();
    reset(0); mac[3] = 29; run_dpd(&dev); assert(captures == 0);
    reset(0); mac[3] = 41; run_dpd(&dev); assert(captures == 0);
    reset(0); external_pa = true; run_dpd(&dev); assert(captures == 0);
    puts("PASS: success, gain oscillation, all idle timeouts, capture timeout, linearity failures, per-chain cleanup, and gates");
}
'''
with tempfile.TemporaryDirectory(prefix='test-dpd-') as tmp:
    source = Path(tmp) / 'test.c'
    exe = Path(tmp) / 'test'
    source.write_text(harness + functions + '\nstatic void run_dpd(struct rt2x00_dev *rt2x00dev) {\n' + caller + '\n}\n' + main)
    subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-sign-compare',
                    '-fsanitize=undefined', str(source), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=5)
