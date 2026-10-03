# rt2x00 TODO: MT7620 PA/LNA detection

## Objective

Determine whether the ZBT-WE826 amplifier-detection problem warrants a general
RT6352 driver correction or a board-specific override, and prepare an upstream
fix supported by hardware evidence.

Current assessment: **potential general driver bug with a verified local
workaround**. Blank `NIC_CONF2` alone does not establish that external PA/LNA
hardware is absent.

## Evidence from our unit

- Board: ZBT-WE826 (16M), MT7620A ver:2 eco:6, using `rt2800soc`.
- `NIC_CONF1` at byte offset `0x36` is `0xc004`: external PA on both TX
  chains and external 2.4 GHz LNA are advertised.
- `NIC_CONF2` at byte offset `0x42` is `0xffff` (unprogrammed).
- Existing detection preserves the `NIC_CONF1` PA flags when `NIC_CONF2`
  is `0` or `0xffff`; otherwise it clears them if the external-PA field is unset.
- Local patch `mac80211-patches/998-rt2x00-dont-trust-external-PA-bits-when-NIC_CONF2-is-blank.patch`
  clears the PA flags for those blank values too.
- The troubleshooting log records the phone's received signal improving from
  -66 to -38 dBm after 998, with control APs changing by at most 1 dB.
  RXIQ calibration failures went from four per boot to zero.
- This is a received-signal measurement, not a calibrated transmitter-power
  measurement. It supports the workaround on this unit, not every RT6352 board.
- Patch 0002's pinmux changes took effect without improving signal. That alone
  does not prove external amplifiers are physically absent.

See `README.md` for the published results and current validation status.

## Investigation

- [ ] Trace the history of RT6352 PA detection, including why `NIC_CONF1`
  remains authoritative when `NIC_CONF2` is blank.
- [ ] Establish the intended meaning of both EEPROM fields for MT7620/RT6352,
  including whether the RT3352-named `NIC_CONF1` bits apply to this chip.
  Use documented formats, driver call paths, and board evidence together.
- [ ] Check current upstream Linux and OpenWrt for subsequent fixes before
  preparing a submission.
- [ ] Confirm our board's actual PA/LNA population using revision-matched
  schematics, component markings, or clear board photographs.
- [ ] Obtain EEPROM values and hardware revisions from other WE826 units.
  Do not assume all WE826 variants share the same RF front end.
- [ ] Collect comparable evidence from RT6352 boards with confirmed external
  amplifiers. Specifically look for a real external PA with blank `NIC_CONF2`.
- [ ] Investigate PA and LNA independently. `NIC_CONF2_EXTERNAL_PA` does not
  directly establish LNA presence.
- [ ] Audit patch 999's actual condition: it clears LNA in the whole PA-clearing
  branch, including valid `NIC_CONF2` with the PA field unset, not only blank
  values. Its title understates its scope.

## Choose the fix from the evidence

- [ ] If field semantics establish a general detection error, correct the
  RT6352 interpretation and document why existing boards remain supported.
- [ ] Otherwise, design a board-specific override using an appropriate
  firmware/device-tree mechanism agreed with maintainers. Keep PA and LNA
  decisions separate; avoid inventing a binding before reviewing conventions.
- [ ] Preserve existing detection for unaffected boards.
- [ ] Treat 998 as a local workaround until its chip-wide scope is justified
  or narrowed. It currently affects every RT6352 board using this driver build.

## Validation and submission

- [ ] A/B test the proposed fix on our unit with fixed placement, channel,
  bandwidth, power settings, and client. Record repeated signal samples,
  throughput in both directions, retries, and calibration logs.
- [ ] Test confirmed external-PA hardware and an unaffected internal-PA board.
- [ ] Test blank `0`, blank `0xffff`, and valid `NIC_CONF2` cases, including
  advertised PA present/absent, against the chosen semantics.
- [ ] Test the working stack without patch 0002 separately before removing it.
- [ ] Keep unrelated DPD/temperature changes out of this submission and hold
  their settings constant during comparisons.
- [ ] Prepare a minimal patch and commit message distinguishing observed
  results from inferred hardware behavior; include exact tested revisions.
- [ ] Discuss the detection ambiguity with rt2x00/OpenWrt maintainers before
  proposing a blanket change.

## Public references checked during review

- [Daniel Golle, external PA support for MT7620, September 2022](https://marc.info/?l=linux-wireless&m=166336921421743&w=2):
  introduces the exact guarded cross-check modified by 998. Explicitly skips
  `0` and `0xffff`; does not justify treating those values as no external PA.
- [Shiji Yang, MT7620 PA/LNA calibration rework, 2023](https://lists.infradead.org/pipermail/lede-commits/2023-September/019295.html):
  related calibration and register handling, not an equivalent blank-field fix.
- [Linksys E1700 PA/LNA pinctrl fix, February 2025](https://lists.infradead.org/pipermail/lede-commits/2025-February/024387.html):
  precedent for 0002 on hardware with external amplifiers; no evidence here
  about that board's `NIC_CONF2` value.
- [X-WRT WE826 instability report #310](https://github.com/x-wrt/x-wrt/issues/310):
  similar symptoms without an EEPROM diagnosis; not confirmation of our cause.

The search found related work but no exact public equivalent of 998. This is
not proof that no such patch exists.
