---
title: Deliberate Tone-dial before/after on the external speakers -- full bass cuts 200Hz THD roughly 2-3x vs. full treble (46-48% down to 15-16% at 75% volume), confirming the Tone setting is a real, previously-unknown confound in every prior measurement this series
status: validated-live
date: 2026-08-30
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 9e827d4; scripts/hardware_profile.py (thd, sweep subcommands)
hardware: same Mac mini M4 + AIRHUG 28 mic + external Z200-class speakers as the rest of the 2026-08-27 through 2026-08-30 series. Speaker volume dial unchanged (~50% of max) for this comparison -- ONLY the Tone dial was moved, from full-"+"/treble (every prior measurement this series) to full bass (this note).
evidence:
  - THD sweep, scripts/hardware_profile.py thd, identical parameters to the same day's earlier full-treble gap-fill run (200/1000/4000Hz x 20/35/50/75% volume x N=3), external speakers, Tone dial moved to full bass immediately beforehand.
  - Raw JSON under /tmp/hardware-profile-loop-20260830/external-thd-full-bass.json (scratch, not committed).
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; noticed the Tone dial on the Z200-class QSG after this same day's earlier 200Hz finding, checked and reported it was set full-"+"/treble, then physically moved it to full bass for this deliberate before/after)
    - Claude Code (Anthropic claude-sonnet-5) -- ran both sweeps, compared, wrote this note
  org: https://legionforge.org
  created: 2026-08-30T00:00:00-05:00
  revised: 2026-08-30T00:00:00-05:00
license: CC BY 4.0 (intent; repo code MIT)
---

# Tone dial before/after: full bass cuts 200Hz THD 2-3x vs. full treble

## Why this run

Earlier the same day, a THD gap-fill sweep found the external speakers'
200Hz distortion climbs sharply with volume (13% at 50%, 48% at 75%)
while 1kHz/4kHz stay clean. Checking the speaker hardware afterward
(prompted by finding a Z200-class quick-start guide, see the 2026-08-28
note's retroactive hardware-ID addendum), JP found a physical "Tone"
dial on the unit, set to its full-"+" position -- his read: full toward
treble, not bass, previously unknown/unchecked, and unchanged across
every measurement in this entire field-note series back to 2026-08-27.
That raised an obvious, cheap-to-answer question: how much of the 200Hz
finding was the driver's own nonlinearity vs. the Tone circuit actively
suppressing/interacting with bass at that setting? JP moved the dial to
full bass and asked for the identical sweep rerun.

## Result: a real, large effect -- full bass roughly halves-to-thirds the 200Hz THD

| volume | 200Hz THD, full treble (earlier today) | 200Hz THD, full bass (this run) |
|---|---|---|
| 20% | unreliable (SNR 8-13dB) | unreliable (SNR 3-11dB) |
| 35% | unreliable (SNR 16-19dB) | unreliable (SNR 18-19dB) |
| 50% | **13.0-14.5%** | **5.3-8.3%** |
| 75% | **46.1-48.4%** | **15.5-15.9%** |

At both volumes where the reading is trustworthy (SNR-gated reliable),
full bass roughly **halves the 200Hz THD at 50% volume, and cuts it to
about a third at 75% volume**, compared to the exact same test at full
treble a few hours earlier. 1kHz and 4kHz stayed clean and closely
similar between the two Tone settings (e.g. 4kHz at 75%: 0.43-0.50%
treble vs. 0.46-0.51% bass -- no meaningful difference), consistent with
a tone control that mainly reshapes the bass/treble balance rather than
changing overall gain or midrange behavior.

## A clarifying, slightly counter-intuitive detail from the ESS frequency-response sweep

Also reran the ESS/Farina sweep (frequency response + RT60) at 50%/75%
volume, full bass, for comparison against the same-day full-treble ESS
data (the RT60 confirmation run):

| band | 50% full treble | 50% full bass | 75% full treble | 75% full bass |
|---|---|---|---|---|
| 100-300Hz | 75.2 dB | 72.6 dB | 84.0 dB | 82.3 dB |
| 300-700Hz | 61.9 dB | 63.0 dB | 73.8 dB | 72.8 dB |
| 700-1500Hz | 62.3 dB | 66.0 dB | 75.6 dB | 75.6 dB |
| 1500-3000Hz | 63.7 dB | 65.0 dB | 76.7 dB | 74.7 dB |
| 3000-5000Hz | 61.4 dB | 61.1 dB | 73.8 dB | 70.8 dB |
| 5000-8000Hz | 67.5 dB | 66.2 dB | 79.0 dB | 75.8 dB |

**The 100-300Hz band's broadband energy did NOT increase at full
bass -- if anything it's marginally lower** (72.6 vs 75.2dB at 50%; 82.3
vs 84.0dB at 75%), and every other band shows similarly small (1-3dB),
inconsistently-directioned differences that look like normal live-
capture measurement variance rather than a clear tonal reshaping. RT60
is essentially identical between settings (0.546s vs 0.549s at 50%;
0.563s vs 0.549s at 75%) -- expected, since room acoustics don't depend
on a speaker's own tone control.

**This means the "Tone" dial is very likely NOT a simple bass-boost
knob** -- boosting low-frequency drive would be expected to show up as
measurably MORE 100-300Hz energy in a broadband sweep, and it doesn't.
The 2-3x THD reduction at 200Hz happened without a corresponding
increase in raw low-frequency output level. More likely explanations,
none confirmed: the dial reduces treble output (a treble-cut, not a
bass-boost, which a broadband energy comparison across FIXED input
level wouldn't clearly distinguish from a bass boost using only relative
band-energy numbers without an absolute reference); or the dial changes
something about the amp's internal EQ/clipping/compensation behavior
that specifically affects harmonic distortion generation without a
proportional change in fundamental output level. **Not resolved here**
-- worth keeping in mind before assuming "full bass = louder bass",
which this data does not support.

## What this means for the earlier 200Hz finding

**Not "pilot error."** The fundamental's SNR was solid in both settings
(21-38dB at 50-75% volume) -- there was a real, measurable signal both
times, and real, measurable distortion both times. What changed is the
MAGNITUDE of that distortion, not whether it existed. The correct
reading: **this driver has genuine, volume-dependent bass distortion,
and the Tone dial's position is a real, independent factor that
modulates how bad it looks at any given volume** -- not a bug in the
tool, not a fluke of one sweep, and not evidence the earlier
measurement was "wrong." Both measurements are valid records of what
was actually happening at each Tone setting.

**Practical implication for every OTHER measurement in this whole
field-note series (2026-08-27 through this morning):** all of them were
collected with the Tone dial at full-"+"/treble, unknown and unchecked
until today. This does not invalidate any of that data -- the AEC/
barge-in grids used real Piper TTS speech, which has relatively little
200Hz-range energy compared to speech-band frequencies, so the
Tone-dial effect on pure 200Hz tones likely did not materially change
those grids' outcomes -- but it is now a known, documented variable for
anyone extending this series, rather than an invisible one.

## What this does NOT show

- Does not identify the tone circuit's actual electrical behavior
  (a passive RC shelving filter vs. something more complex) -- treated
  as an empirical before/after, not a characterized mechanism.
- Does not retest 20%/35% volume with adequate SNR at either Tone
  setting -- both remain unmeasurable at this dial's normal listening
  levels, same limitation as every other THD measurement in this
  series.
- Does not test intermediate Tone positions (only the two extremes) --
  the relationship between dial position and THD could be linear,
  threshold-like, or something else; not characterized.
- Does not re-verify the AEC/barge-in grids at the new Tone setting --
  out of scope for this note; flagged only as a documented variable
  above, not retested.

## Recommended follow-ups (not started)

1. If anyone continues acoustic testing on this exact speaker pair,
   record and hold the Tone dial position going forward (default to
   full bass, since it measurably reduces distortion at no apparent
   midrange cost) rather than leaving it as an unrecorded variable.
2. An intermediate Tone sweep (e.g. quarter/half/three-quarter
   positions) would characterize the actual response curve, if ever
   worth the effort -- low priority given the AEC/barge-in conclusion
   above is unlikely to change.
