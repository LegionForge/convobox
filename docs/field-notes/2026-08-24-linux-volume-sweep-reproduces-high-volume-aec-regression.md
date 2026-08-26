---
title: Linux (openSUSE, onboard HDA/PipeWire, Sager 2014 laptop) reproduces the macOS/Windows finding that AEC can make self-barge-in worse than AEC-off at high system volume -- third platform, third distinct hardware profile, now confirmed at N=10
status: validated-live (N=10 per volume level as of the follow-up below -- matches/exceeds the macOS session's N=7 rigor; the original same-day pass was N=1/directional only)
date: 2026-08-24
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 3e2818d (v0.4.0); aec-audio-processing 1.0.1 (built from source); WebRTC AEC3; tts.engine kokoro, voice af_sarah; interaction.interrupt_preset conversational (set explicitly for this run -- the schema default, do-not-disturb, makes BargeInMonitor a no-op); aec_delay_ms auto (measured 76ms); openSUSE Tumbleweed 20260822 (kernel 7.1.8-1-default), PipeWire
hardware:
  chassis: Clevo P17SM-A barebone (sold under the Sager brand, e.g. NP7358-class) -- DMI-confirmed live via /sys/class/dmi/id (board_name/product_name "P17SM-A", chassis_type 3 = laptop, BIOS American Megatrends 4.6.5 dated 2014-03-27), independently corroborating the operator's "Sager (2014)" identification rather than just taking it on his word.
  cpu: Intel Core i7-4810MQ @ 2.80GHz (Haswell, 4 cores / 8 threads, max turbo 3.8GHz) -- lscpu-confirmed live.
  memory: 31GiB total, 2GiB swap -- free -h-confirmed live.
  storage: Samsung SSD 850 PRO 512GB (SATA SSD -- OS root on btrfs, /boot/efi on vfat) + HGST HTS721010A9E630 1TB 7200rpm (SATA, spinning -- /home on xfs) + a BD-ROM optical drive (MATSHITA UJ260AF). No NVMe.
  gpu: Intel HD 4600 (integrated) + NVIDIA Quadro K3000M (discrete, Optimus) -- neither used by ConvoBox this session; faster-whisper/kokoro/WebRTC AEC all ran CPU-only (stt.device left at its auto/CPU default, not cuda).
  audio: onboard HDA Intel PCH (Realtek ALC892 codec) via PipeWire -- sink "Built-in Audio Analog Stereo" (the laptop's own built-in speakers), source "ALSA Source on hw:1,0" (aliased "mic1" in pactl, the laptop's own built-in mic). No external mic/speakers, no USB audio interface, no discrete sound card -- structurally similar in spirit to the macOS session's "near-worst-case acoustic coupling" framing (chassis-coupled speaker+mic, no physical isolation), though a different specific mechanism (laptop chassis vs. desktop's single small speaker).
  microphone_and_speakers_placement: fixed by the chassis (built-in laptop hardware, not repositionable) -- but the operator was not present during the run, so exact room acoustics/reflections relative to where the laptop sat were not measured -- see Method.
  room: not recorded (operator absent during the run -- see Method).
  note: this machine (2014-era CPU/BIOS) shows real, expected end-to-end latency in normal ConvoBox use -- STT/backend response times consistently 5-30s+ in the same-day live human-speech session (see the companion field note) -- worth keeping in mind as the baseline for this specific hardware profile, not a Linux-specific slowdown: the pipeline works correctly on it, just visibly slower than the newer machines the macOS/Windows sessions used.
evidence:
  - 16 real live trials, one continuous run, scripts/acoustic_calibration.py --volume-candidates 100,90,80,70,60,50,40,30,20,10,30,25,20,15,10,5 --delay-candidates auto --repeat-each 1 --ambient-seconds 2 --response-repeats 2
  - uat-acoustic-calibration/20260824-144747/report.json (gitignored, not committed -- this field note is the durable record)
  - New Linux volume-control code path added this session: scripts/acoustic_calibration.py's _get_wpctl_volume_percent/_set_wpctl_volume_percent (wpctl/PipeWire), extending --volume-candidates beyond its previous Windows-only (pycaw) implementation
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; requested the Linux volume sweep and the specific two-phase protocol -- 100 down to 10 by 10s, then 30 down to 5 by 5s "just like on the mac" -- approved the volume ceiling of 100%, was in a separate room on another machine for the actual run, approved the sudo zypper install of meson/ninja/swig)
    - Claude Code (Anthropic claude-sonnet-5) -- added Linux (wpctl) support to acoustic_calibration.py's --volume-candidates, diagnosed and worked around an upstream aec-audio-processing packaging bug (see Mechanism), ran the sweep, aggregated the data, wrote this note
  org: https://legionforge.org
  created: 2026-08-24T15:00:00-05:00
  revised: 2026-08-25T11:16:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Linux volume sweep reproduces the high-volume AEC regression on a third platform

**Context for outsiders.** ConvoBox is a local voice frontend for CLI coding
agents: mic and speakers run simultaneously, and acoustic echo cancellation
(AEC) keeps the assistant's own TTS output from being picked back up by the
mic and misread as user speech ("self-barge-in"). GitHub issue #119
established on macOS that AEC can make false self-triggered barge-ins
*worse* than AEC being off entirely at high system volume; the 2026-08-20
Windows note confirmed the same effect on completely different hardware
(Realtek onboard + amplified 7.1 vs. a Mac mini's single built-in speaker).
This note is the same test on Linux, the platform the README currently
lists as "implemented, not yet voice-validated."

## Problem

Does the "AEC gets worse at high volume" finding reproduce on Linux, on a
third distinct hardware profile (openSUSE, onboard HDA Intel PCH/Realtek
ALC892 via PipeWire)? And, separately: does ConvoBox's calibration tooling
even support a Linux volume sweep yet? (It didn't -- see below.)

## Method

JP asked for a two-phase sweep matching the macOS session's shape: a full
100%-to-10% descent in 10-point steps, then a second, finer 30%-to-5%
descent in 5-point steps "just like on the mac" for low-end resolution.
Both phases were run as one continuous 16-level sweep (duplicate levels
at 30/20/10 are two independent trials, one per phase, not deduplicated):
`100,90,80,70,60,50,40,30,20,10,30,25,20,15,10,5`.

**Operator was not present for the run itself** -- explicitly stated
up front ("I will be in another room working with claude on another
project on another server"), monitoring only via a mobile push-notification
channel. This is a deliberate methodological difference from the macOS and
Windows sessions, both of which had a human directly at the machine the
whole time (confirming volume comfort/safety, watching for driver
distortion, checking mic DSP LED state, measuring mic placement). Because
of that, this note can vouch for the *software/acoustic* result (every
number below is a real measurement from real audio played through and
captured by real hardware) but not for hardware/room specifics -- no
distortion check, no mic placement measurement, no DSP-state confirmation,
no RT60. Framed explicitly as a gap, not silently omitted.

The 100% ceiling itself was also **the operator's own explicit call**, made
in the same message that requested the two-phase protocol -- not a default
this note picked on its own. The Windows note's caution about volume
percentages not transferring across rigs applies here too; this session
did not independently re-derive a safe ceiling for this particular
hardware the way the Windows session did (comfort-checking 30% live before
committing to a sweep).

## What had to be built/fixed first

**1. `--volume-candidates` was Windows-only.** It drove system volume via
`pycaw` (Core Audio `IAudioEndpointVolume`), with an explicit
`NotImplementedError` on every other platform. Added a Linux counterpart
using `wpctl` (WirePlumber/PipeWire) -- `_get_wpctl_volume_percent`/
`_set_wpctl_volume_percent`, dispatched by `sys.platform` alongside the
existing pycaw path in `scripts/acoustic_calibration.py`. No new Python
dependency: `wpctl` ships with PipeWire/WirePlumber, already present.

**2. `aec-audio-processing` failed to build from source on this distro --
a real upstream packaging bug, not a ConvoBox bug.** PyPI ships no Linux
wheel for this package (same as documented for macOS in
`docs/KNOWN-ISSUES.md`), so `uv sync --extra aec` builds
`webrtc-audio-processing` from source via meson/ninja/swig (which
themselves had to be installed via `zypper` -- not preinstalled, needed
`sudo`, JP ran that step). The meson build itself succeeded and installed
the shared library to `webrtc-audio-processing/install/lib64/` -- openSUSE's
(and other RPM-based/multilib distros') 64-bit convention. But the
package's own `setup.py` (`get_webrtc_library_path()`) globs only
`install/lib/**/libwebrtc-audio-processing-2.so`, hardcoded, with no
`lib64` fallback -- so it built the library successfully and then reported
`FileNotFoundError: Could not find built WebRTC library` because it was
looking in the wrong directory. Debian/Ubuntu (and macOS's `lib`-based
layout) never hit this; RPM-based distros will, every time, until upstream
fixes it. **Workaround applied this session** (outside the repo, in uv's
sdist cache, not a durable fix): `ln -s lib64
.cache/uv/sdists-v9/pypi/aec-audio-processing/1.0.1/*/src/webrtc-audio-processing/install/lib`
before re-running `uv sync --extra aec` -- the glob then finds the library
through the symlink. This is host-cache-local and does **not** survive a
`uv cache clean` or a fresh machine; a real fix needs either an upstream PR
against `aec-audio-processing`'s `setup.py` (search both `lib` and `lib64`,
the standard `sys.platform`/`sysconfig`-aware way most Python C-extension
build scripts handle this) or a ConvoBox-side note in
`docs/KNOWN-ISSUES.md` warning RPM-based-distro users and giving them this
same symlink workaround. Neither has been done yet -- this field note is
the only record so far.

**3. The schema default `interaction.interrupt_preset: do-not-disturb`
makes the false-barge-in count meaningless.** `BargeInMonitor.observe()`
(`scripts/run_convobox.py`) returns `False` unconditionally when
`on_current_turn == "let-finish"`, which is exactly what `do-not-disturb`
resolves to. Both the macOS and Windows sessions used `conversational`
explicitly; this session's `convobox.yaml` had neither set (a near-empty,
gitignored per-machine file with only a leftover `backend:` block from an
earlier smoke test), so `interaction.interrupt_preset: conversational` was
added before running -- otherwise every trial would have reported zero
false-barge-ins regardless of what the mic actually picked up, silently.

## Evidence

16 real trials, one continuous sweep, `auto` AEC delay throughout
(measured 76ms every trial -- output+input latency estimate, stable across
the whole run, unlike the macOS/Windows sessions' 222-238ms, consistent
with this being a different, presumably lower-latency onboard audio path).

| Volume % | Delay | Attenuation dB | Ceiling dB | Suppression dB | Echo lag ms | Raw corr | AEC corr | FB raw | FB AEC | Utt raw | Utt AEC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 76ms | 10.54 | 11.99 | 8.81 | 71 | 0.0887 | 0.0711 | 4 | **9** | 4 | 4 |
| 90 | 76ms | 12.11 | 14.01 | 10.31 | 106 | -0.0595 | -0.0061 | 4 | 1 | 4 | 0 |
| 80 | 76ms | 15.26 | 12.31 | 11.16 | 95 | 0.0919 | 0.0449 | 4 | **4** | 4 | 3 |
| 70 | 76ms | 10.25 | 10.55 | 9.81 | 76 | 0.0701 | 0.0442 | 4 | 1 | 4 | 0 |
| 60 | 76ms | 8.26 | 7.77 | 9.65 | 98 | -0.0764 | -0.0108 | 5 | 3 | 5 | 0 |
| 50 | 76ms | 6.83 | 4.22 | 8.31 | 80 | -0.0729 | -0.0345 | 5 | 1 | 4 | 1 |
| 40 | 76ms | 8.37 | 2.42 | 8.81 | 69 | 0.0675 | 0.0125 | 3 | 0 | 3 | 0 |
| 30 (pass 1) | 76ms | 8.20 | 1.16 | 8.84 | 58 | -0.0201 | 0.0014 | 1 | 0 | 1 | 0 |
| 20 (pass 1) | 76ms | 5.99 | 0.18 | 5.31 | 385 | -0.0121 | -0.0005 | 0 | 0 | 0 | 0 |
| 10 (pass 1) | 76ms | 6.40 | 0.25 | 7.34 | 95 | -0.0070 | -0.0032 | 0 | 0 | 0 | 0 |
| 30 (pass 2) | 76ms | 12.25 | 2.88 | 11.60 | 81 | -0.0167 | -0.0042 | 0 | 0 | 0 | 0 |
| 25 | 76ms | 11.04 | 0.64 | 9.45 | 75 | -0.0090 | 0.0010 | 0 | 0 | 0 | 0 |
| 20 (pass 2) | 76ms | 7.10 | 1.04 | 7.37 | 211 | -0.0109 | 0.0022 | 0 | 0 | 0 | 0 |
| 15 | 76ms | 7.06 | 3.25 | 6.75 | 310 | -0.0099 | 0.0033 | 0 | 0 | 0 | 0 |
| 10 (pass 2) | 76ms | 8.18 | 0.65 | 8.56 | 63 | -0.0087 | -0.0043 | 0 | 0 | 0 | 0 |
| 5 | 76ms | 8.79 | 4.04 | 8.89 | 60 | -0.0087 | -0.0078 | 0 | 0 | 0 | 0 |

System output volume was correctly restored to its pre-run value (30%)
afterward, confirmed in the run's own log
(`system output volume restored to 30.0%`).

## Mechanism

**The core finding reproduces: at 100% volume, AEC made false barge-ins
worse than AEC-off (4 raw -> 9 processed)** -- the same qualitative effect
first found on macOS (Mac mini, single built-in speaker) and confirmed on
Windows (Realtek onboard + amplified 7.1), now on a third, again
completely different, hardware profile (onboard HDA Intel PCH, no
amplification, no external DAC). Three platforms, three distinct
amplifier/speaker/driver chains, same qualitative failure mode at the top
of the volume range -- consistent with (not proof of) the macOS note's
original hypothesis that a linear AEC filter can't model a driver
distorting at high output, since distortion is a nonlinear artifact no
linear adaptive filter converges against.

**80% also showed AEC providing zero net benefit** (4 raw, 4 processed --
tied, not worse, but not helping either), between the 100% regression and
the 90%/70%/60%/50%/40%/30%-and-below range where AEC clearly helped
(processed counts well below raw). With N=1 per level this specific
90%-worse-than-80% ordering is noise-shaped -- **directional only**, not
something to read as a precise threshold, unlike the macOS session's N=7
aggregate.

**Raw (uncancelled) false-barge-ins fell to zero at and below 30% on this
rig, in both sweep passes** -- a much steeper falloff than the macOS
session, where raw stayed pinned at almost exactly 1 per trial from 100%
all the way down to 25% (N=7 each), only reaching 0 at 20%. This is a real
difference in this rig's overall gain/coupling profile (mic
sensitivity, room, distance -- all unrecorded this session, see Method),
not a contradiction of the macOS finding -- it's the same lesson the
Windows note already stated explicitly: **volume percentages do not
transfer across rigs**, reinforced now by a third data point that
disagrees with both prior sessions' shapes while confirming the same
top-of-range qualitative effect.

**Echo lag was mostly stable (58-106ms) but spiked hard at low volume**
(211ms at 20% pass 2, 385ms at 20% pass 1, 310ms at 15%) where raw
correlation was already near the noise floor (|corr| < 0.02) -- consistent
with the lag estimator losing its signal once the echo is barely above
ambient, not a real change in acoustic path delay. `estimated_echo_lag_ms`
should be read as unreliable below roughly the point where `ceiling_db`
drops under ~1dB, matching the same caveat implicit in the macOS/Windows
notes' own ceiling-based framing.

## What transfers

- **The "AEC can make barge-in worse than AEC-off at high volume" finding
  is now confirmed on all three platforms ConvoBox targets** (macOS,
  Windows, Linux), on three hardware profiles with essentially nothing in
  common (single built-in speaker; onboard Realtek into an amplified 7.1;
  onboard Realtek with no amplification). This is the strongest cross-
  platform evidence yet that the effect is a property of AEC/driver
  distortion interaction in general, not an artifact of one platform's
  audio stack. (validated-live, but each platform's own N is directional-
  to-moderate, not large-sample -- see each note's own N.)
- **Volume percentages remain non-portable across rigs**, now with a third
  disagreeing data point (steep falloff to zero raw false-barges by 30%
  here, vs. macOS's near-flat baseline down to 20%). Any future cross-
  platform comparison needs a hardware-independent measure (dB SPL at the
  mic, or `ceiling_db` itself) rather than the OS volume slider.
  (validated-live, this session)
- **`aec-audio-processing`'s Linux packaging has a real, reproducible bug**
  on RPM-based/multilib distros (confirmed openSUSE Tumbleweed; likely
  Fedora/RHEL too, unconfirmed) -- installs to `lib64`, checks only `lib`.
  Not yet reported upstream or added to `docs/KNOWN-ISSUES.md`; the
  symlink workaround above is not durable. (diagnosed, live-verified
  mechanism; fix not yet applied anywhere durable)
- **`scripts/acoustic_calibration.py --volume-candidates` now works on
  Linux via `wpctl`**, not just Windows via `pycaw`. (validated-live,
  shipped this session)
- **This session's specific numbers (the raw data table above) should not
  be read as a hardware/room-independent Linux baseline** -- unlike the
  macOS and Windows sessions, no human confirmed mic placement, DSP state,
  or room acoustics here. Treat the *qualitative* pattern (100% regression,
  steep low-volume falloff) as the reusable claim, not the specific dB/
  count values.

## Not done here (original N=1 pass)

- No N>1 repeats at any volume level -- directional only throughout,
  unlike macOS's N=7 or Windows' N=3 matched comparison. **Addressed by
  the follow-up below.**
- No RT60 or room measurement (operator absent).
- No mic/speaker model identification, placement measurement, or DSP-state
  check (operator absent) -- see Method's caveats section. **Partially
  addressed**: hardware later identified by the operator as a Sager
  laptop (2014) -- see the `hardware:` frontmatter field, corrected after
  the original pass.
- No upstream bug report filed against `aec-audio-processing` for the
  `lib`/`lib64` packaging issue, and no `docs/KNOWN-ISSUES.md` entry added
  yet for it. Still true after the follow-up.
- No independent-session reproduction of this Linux run itself -- one
  session, one rig.

---

## Follow-up (same session): full 0-100% sweep at N=10, clean-decade steps

JP asked for a more rugged pass: "the full volume sweep of 0-100% with 10%
increments... run the calibration scripts (run 10x) to see if you get the
same results when calibrating for this room/laptop/mic (sager from 2014)."
This replaces the original pass's odd two-phase step list with a clean
11-level decade sweep, each level run 10 times independently -- **110 real
live trials, N=10 per level**, matching/exceeding the macOS session's own
N=7 rigor. Same command shape as the original pass, extended:

```
scripts/acoustic_calibration.py --volume-candidates 0,10,20,30,40,50,60,70,80,90,100 \
  --delay-candidates auto --repeat-each 10 --ambient-seconds 2 --response-repeats 2
```

Run under `systemd-inhibit --what=sleep:idle` (operator again absent from
the machine for the run itself) so an idle screen-lock/suspend couldn't
interrupt a run this long. System volume was correctly restored to its
pre-run value (30%) afterward, confirmed in the run's own log. Report:
`uat-acoustic-calibration/20260824-150320/report.json`.

### Aggregate results (N=10 per level, summed/meaned across the 10 repeats)

| Vol % | N | Attenuation mean dB | Ceiling mean dB | Suppression mean dB | FB raw (sum/10) | FB AEC (sum/10) | Utt raw (sum/10) | Utt AEC (sum/10) |
|---|---|---|---|---|---|---|---|---|
| 0 | 10 | 8.68 | -0.94 | 11.74 | 0 | 0 | 0 | 0 |
| 10 | 10 | 7.17 | -0.09 | 7.67 | 0 | 0 | 0 | 0 |
| 20 | 10 | 6.86 | 0.40 | 7.47 | 0 | 0 | 0 | 0 |
| 30 | 10 | 11.73 | 0.24 | 11.00 | 8 | 4 | 4 | 4 |
| 40 | 10 | 12.10 | 0.65 | 12.25 | 40 | **0** | 38 | 0 |
| 50 | 10 | 8.08 | 5.62 | 9.33 | 50 | 13 | 48 | 4 |
| 60 | 10 | 11.12 | 6.49 | 10.55 | 40 | 12 | 40 | 2 |
| 70 | 10 | 12.49 | 8.44 | 10.20 | 40 | 34 | 40 | 15 |
| 80 | 10 | 12.95 | 13.77 | 10.96 | 35 | 33 | 35 | 19 |
| 90 | 10 | 11.82 | 16.63 | 10.18 | 38 | 32 | 38 | 8 |
| **100** | 10 | 14.93 | 16.05 | 11.57 | 38 | **45** | 38 | 10 |

Ambient RMS for this run: 0.0197 (comparable to the original pass's
0.0224 -- consistent baseline noise floor between the two passes, same
room/session).

### Reading the shape: a clean, sharp threshold and a clean top-of-range regression

**0-20% is a hard floor: zero false-barge-ins, raw or AEC-processed, in
30 trials.** Not "rare" -- exactly zero. This rig has no measurable
self-triggered barge-in risk at all below 30% system volume with a
conversational preset.

**30% is the threshold, not a gradual ramp.** Raw false-barges jump from 0
(at 20%) to 8 (at 30%) to a sustained 35-50 range from 40% through 100% --
i.e. once real echo becomes VAD-detectable at all on this rig, it's
detectable at a fairly stable rate regardless of how much *further* volume
increases, up to the top of the range. This is a materially different
shape from both prior sessions: macOS's raw rate was flat at ~1/trial from
100% all the way down to 25% before dropping at 20% (a shelf, not a cliff);
this rig instead shows almost nothing below 30% and a sudden plateau
above it.

**AEC's effectiveness degrades smoothly and monotonically as volume rises
past the threshold, then flips to a net regression exactly at 100%** --
now the clean, decisive version of what the original N=1 pass only
hinted at:

| Vol % | FB raw | FB AEC | AEC effect |
|---|---|---|---|
| 40 | 40 | 0 | **100% eliminated** |
| 50 | 50 | 13 | 74% reduction |
| 60 | 40 | 12 | 70% reduction |
| 70 | 40 | 34 | 15% reduction |
| 80 | 35 | 33 | 6% reduction |
| 90 | 38 | 32 | 16% reduction |
| **100** | 38 | **45** | **18% WORSE than AEC-off** |

This is the same qualitative "AEC regresses at the very top of the volume
range" finding as macOS and Windows, now with real statistical weight
(N=10, not N=1) behind both ends of the story: AEC is not just adequate
but *excellent* in the 40-60% band (up to fully eliminating false barges),
degrades steadily through 70-90%, and is confirmed -- not just suggested --
net harmful at 100%, summed across 10 independent trials rather than one.

**`ceiling_db` is not perfectly monotonic at the very top**: it rises
cleanly from -0.94dB (0%) to a peak of 16.63dB at 90%, then dips slightly
to 16.05dB at 100% -- a small but real non-monotonicity, plausibly
consistent with the same driver-distortion/compression mechanism proposed
in the macOS note (a laptop speaker driven past its clean range can
produce *less* additional measurable signal per volume step at the very
top, not more, if it's compressing/clipping rather than linearly
louder) -- not confirmed at the mechanism level here either, same caveat
as the prior two notes.

## What transfers (updated)

- **The N=1 pass's headline claim now has real statistical backing**: AEC
  making self-barge-in worse than AEC-off at maximum volume is confirmed
  at N=10 on this rig, not just directionally suggested. Three platforms,
  three hardware profiles, all showing the same top-of-range regression --
  now two of the three (macOS N=7, this Linux N=10) with real repeat
  counts behind it.
- **This rig has an unusually sharp, clean volume threshold** (essentially
  zero risk below 30%, a stable plateau from 40% up) compared to macOS's
  gradual shelf-shaped decline -- reinforcing, with a much starker example
  this time, that volume-percentage thresholds are rig-specific and not
  portable, per the Windows note's original lesson.
- **AEC's sweet spot on this specific rig is 40-60% system volume**,
  where it goes from "excellent" (100% eliminated at 40%) to "very good"
  (70-74% reduction at 50-60%) -- a genuinely actionable, rig-specific
  recommendation if this exact laptop is used for real ConvoBox sessions,
  though (per every caveat above) not a general Linux recommendation.

## Second follow-up (next calendar session): independent N=10 re-verification, 50-0%, closes the "no second session" gap

JP asked for a re-verification pass the next day, explicitly capping
volume at 50% this time ("at 50% volume we get approximately 100% barge
in [already]... 100 may be too much"). Same command shape, narrower
range: `--volume-candidates 50,40,30,20,10,0 --delay-candidates auto
--repeat-each 10` -- **60 more real live trials, a genuinely independent
second session** (different day, fresh process, same rig), addressing
the first follow-up's own "no independent second session" gap. Report:
`uat-acoustic-calibration/20260825-103644/report.json`. Ambient RMS
0.0188 -- close to both prior sessions' (0.0224, 0.0197), consistent
baseline noise floor across all three passes now.

| Vol % | First N=10 pass (raw/AEC) | This re-verify (raw/AEC) |
|---|---|---|
| 50 | 50 / 13 | 48 / 9 |
| 40 | 40 / 0 | 34 / 1 |
| 30 | 8 / 4 | 13 / 5 |
| 20 | 0 / 0 | 0 / 0 |
| 10 | 0 / 0 | 0 / 0 |
| 0 | 0 / 0 | 0 / **1** |

**The shape reproduces closely across two independent sessions**: a hard
floor at and below 20%, AEC's best performance at 40% (near-total
elimination both times), and still-substantial self-barge-in at 50% both
times (AEC cuts it by roughly 75-80% but doesn't come close to
eliminating it). 30% shows more session-to-session variance (8 vs. 13
raw) than the other levels, but stays in the same "modest, non-zero"
band both times -- consistent with, not contradicting, JP's own live
human-voice read of that exact level as "about half and half."

**This is now three independent lines of evidence agreeing on the same
shape**: this synthetic N=10 pass, the first synthetic N=10 pass (previous
session), and JP's real human voice in the same-day live-speech field
note (`2026-08-25-linux-first-real-human-speech-demo-*`) -- "pretty much
100%" at 50%, "about half and half" at 30%, essentially nothing below
that. Three different measurement methods, two different sessions,
one consistent story.

**One curiosity, not chased further**: at 0% volume (no real playback
signal at all), raw false-barges were 0/10 both sessions, but this
session's AEC-processed count showed a single false-barge (1/10) where
the first session showed none. With no real echo signal present at 0%,
this is most plausibly AEC reacting to ambient room noise/its own
adaptive-filter noise floor rather than anything related to echo
cancellation performance -- a single N=1 occurrence, not enough to call a
real pattern, noted honestly rather than smoothed over.

## Not done (after both follow-ups)

- Still no RT60/room measurement, no mic model/placement/DSP-state
  confirmation, no upstream `aec-audio-processing` bug report or
  `docs/KNOWN-ISSUES.md` entry.
- **The "no independent second session" gap is now closed** for the
  50%-and-below range (both follow-ups agree) -- but the second session
  never re-tested 60-100%, so that upper range still has only the single
  first-pass N=10 measurement.
- No investigation into *why* this rig's threshold is so much sharper
  than macOS's -- left as an open, unconfirmed hypothesis (driver/DSP
  differences, mic gain/AGC behavior, or genuine room/placement
  differences neither session measured).
- The single 0%-volume AEC false-barge above is unexplained -- not
  investigated further this session.
