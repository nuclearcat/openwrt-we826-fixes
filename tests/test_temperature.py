#!/usr/bin/env python3
# SPDX-License-Identifier: 0BSD
"""Exercise 999b's actual temperature and DPD state code with fake hardware."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
patch = next((root / 'mac80211-patches').glob('999b-*.patch')).read_text()
def additions(text):
    return '\n'.join(x[1:] for x in text.splitlines() if x.startswith('+') and not x.startswith('+++'))
code, header = patch.split('--- a/drivers/net/wireless/ralink/rt2x00/rt2800lib.h')
code = additions(code)
def function(name):
    match = re.search(r'static [\w ]+\s+' + name + r'\([^;]*?\)\n\{', code)
    assert match, name
    end = match.end()
    depth = 1
    while depth:
        depth += (code[end] == '{') - (code[end] == '}')
        end += 1
    return code[match.start():end] + '\n'
enum = re.search(r'enum rt2800_dpd_result \{.*?\};', code, re.S).group()
constants = '\n'.join(x for x in code.splitlines() if x.startswith('#define RT2800_DPD_REDO_'))
preamble = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
typedef uint8_t u8;
typedef int8_t s8;
typedef uint32_t u32;
#define HZ 100
#define TX_ALC_CFG_0 0
#define TX_ALC_CFG_0_CH_INIT_0 0xff
#define EEPROM_TXPOWER_INIT 0
#define time_before(a,b) ((long)((a) - (b)) < 0)
#define rt2x00_dbg(...) ((void)0)
#define usleep_range(a,b) ((void)0)
'''
structs = '\nstruct rt2800_drv_data {\n' + additions(header) + '\n};\n'
mocks = r'''
struct rt2x00_dev { struct rt2800_drv_data *drv_data; struct { u8 tx_chain_num; } default_ant; };
static struct rt2800_drv_data data;
static struct rt2x00_dev dev = { &data, {2} };
static unsigned long jiffies;
static unsigned chain_calls, bias_writes, clear_mask, page;
static bool pa, lna, sensor_busy, good[2];
static s8 temperature;
static u8 ref;
static unsigned power;
static bool rt2x00_has_cap_external_pa(struct rt2x00_dev *d) { return pa; }
static bool rt2x00_has_cap_external_lna_bg(struct rt2x00_dev *d) { return lna; }
static unsigned rt2800_eeprom_read(struct rt2x00_dev *d, unsigned r) { return (ref << 8) | 17; }
static u8 rt2800_bbp_read(struct rt2x00_dev *d, unsigned r) { return r == 47 ? (sensor_busy ? 0x10 : 0) : (u8)temperature; }
static void rt2800_bbp_write(struct rt2x00_dev *d, unsigned r, u8 v) {
    if (r == 186) page = v;
    if (r == 189) clear_mask |= 1u << page;
}
static void rt2800_rfcsr_write_bank(struct rt2x00_dev *d, unsigned b, unsigned r, u8 v) { ++bias_writes; }
static unsigned rt2800_register_read(struct rt2x00_dev *d, unsigned r) { return power; }
static unsigned rt2x00_get_field32(unsigned v, unsigned mask) { return v & mask; }
static bool rt2800_dpd_calibrate_chain(struct rt2x00_dev *d, u8 chain) { ++chain_calls; return good[chain]; }
'''
main = r'''
static void reset(void) {
    memset(&data, 0, sizeof(data)); jiffies = 0; chain_calls = bias_writes = clear_mask = 0;
    pa = lna = sensor_busy = false; good[0] = good[1] = true;
    temperature = 10; ref = 0xff; power = 34; dev.default_ant.tx_chain_num = 2;
}
int main(void) {
    reset(); rt2800_temperature_tick(&dev);
    assert(data.dpd_valid && data.dpd_temp == 10 && chain_calls == 2 && bias_writes == 0);
    /* Attempt at jiffies zero still rate-limits a large temperature change. */
    temperature = 16; jiffies = 5999; rt2800_temperature_tick(&dev); assert(chain_calls == 2);
    jiffies = 6000; rt2800_temperature_tick(&dev); assert(chain_calls == 4 && data.dpd_temp == 16);
    jiffies = 12000; temperature = 21; rt2800_temperature_tick(&dev); assert(chain_calls == 4);
    temperature = 22; good[1] = false; rt2800_temperature_tick(&dev);
    assert(!data.dpd_valid && data.dpd_temp == 16 && chain_calls == 6 && clear_mask == (1u << 4));
    jiffies = 17999; rt2800_temperature_tick(&dev); assert(chain_calls == 6);
    good[1] = true; jiffies = 18000; rt2800_temperature_tick(&dev);
    assert(data.dpd_valid && data.dpd_temp == 22 && chain_calls == 8);
    /* Power skip keeps the old baseline and retries without further drift. */
    power = 29; temperature = 28; jiffies = 24000; rt2800_temperature_tick(&dev);
    assert(!data.dpd_valid && data.dpd_temp == 22 && data.dpd_last_attempt == 24000);
    power = 34; jiffies = 29999; rt2800_temperature_tick(&dev); assert(chain_calls == 8);
    jiffies = 30000; rt2800_temperature_tick(&dev); assert(data.dpd_valid && chain_calls == 10);
    reset(); sensor_busy = true; rt2800_temperature_tick(&dev); assert(!data.dpd_attempted && chain_calls == 0);
    reset(); pa = true; rt2800_temperature_tick(&dev); assert(!data.dpd_attempted && bias_writes == 0);
    reset(); good[0] = good[1] = false;
    assert(rt2800_run_dpd_calibration(&dev) == RT2800_DPD_FAILED);
    assert(clear_mask == ((1u << 2) | (1u << 4)) && !data.dpd_valid);
    reset(); power = 41; assert(rt2800_run_dpd_calibration(&dev) == RT2800_DPD_SKIPPED);
    reset(); dev.default_ant.tx_chain_num = 1; good[1] = false;
    assert(rt2800_run_dpd_calibration(&dev) == RT2800_DPD_SUCCESS && chain_calls == 1);
    /* Signed reference, hysteresis, and forced reapplication on reinit. */
    reset(); ref = (u8)-10;
    rt2800_temperature_compensate(&dev, -13, false); assert(data.temp_low && bias_writes == 4);
    rt2800_temperature_compensate(&dev, -8, false); assert(data.temp_low && bias_writes == 4);
    rt2800_temperature_compensate(&dev, -7, false); assert(!data.temp_low && bias_writes == 8);
    rt2800_temperature_compensate(&dev, -7, true); assert(bias_writes == 12);
    ref = 0; rt2800_temperature_compensate(&dev, -3, false); assert(data.temp_low && bias_writes == 16);
    ref = 0xff; rt2800_temperature_compensate(&dev, 127, true); assert(bias_writes == 16);
    ref = 0; lna = true; rt2800_temperature_compensate(&dev, 127, true); assert(bias_writes == 16);
    lna = false; pa = true; rt2800_temperature_compensate(&dev, 127, true); assert(bias_writes == 16);
    /* Signed raw extremes cannot wrap the relative drift calculation. */
    reset(); temperature = -128; rt2800_temperature_tick(&dev);
    temperature = 127; jiffies = 6000; rt2800_temperature_tick(&dev); assert(chain_calls == 4);
    /* Retry deadline works across jiffies wraparound. */
    reset(); jiffies = ULONG_MAX - 3000; good[0] = false; rt2800_temperature_tick(&dev);
    jiffies = 2998; rt2800_temperature_tick(&dev); assert(chain_calls == 2);
    jiffies = 2999; rt2800_temperature_tick(&dev); assert(chain_calls == 4);
    puts("PASS: DPD outcomes, validity, steady-temperature retries, rate limiting, raw drift, factory reference, bias gates and hysteresis");
}
'''
functions = ''.join(function(n) for n in ['rt2800_read_temperature', 'rt2800_temperature_compensate', 'rt2800_run_dpd_calibration', 'rt2800_temperature_tick'])
with tempfile.TemporaryDirectory(prefix='test-temperature-') as tmp:
    source = Path(tmp) / 'test.c'; exe = Path(tmp) / 'test'
    source.write_text(preamble + structs + mocks + enum + '\n' + constants + '\n' + functions + main)
    subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-fsanitize=undefined', str(source), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=5)
