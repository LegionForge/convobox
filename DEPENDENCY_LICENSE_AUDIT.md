# Dependency license audit: can ConvoBox stay a clean MIT project?

## Status: issue identified, not yet fixed

**Decision (2026-07-10): ConvoBox stays MIT, free for everyone —
personal and commercial use alike, no paid tier — matching the permissive
(MIT/BSD/Apache-2.0) spirit of the dependencies it's built on.** Ongoing
development is optionally supported via Patreon/Ko-fi, not a commercial
license (an earlier split-license plan was researched and explicitly
decided against — see the git history of this repo around 2026-07-10 if
that reasoning is ever needed again).

This audit was originally prompted by a question about commercial
licensing, but its actual finding stands independent of that: **does
ConvoBox's own dependency tree let it stay a single, simple, unencumbered
MIT project?** Not quite, as currently built — `piper-tts` is
GPL-3.0-or-later and imported directly in-process, which means
*distributing* ConvoBox with `piper-tts` as a dependency pulls the whole
combined work under GPL obligations, not clean MIT. That's not a
"blocked from selling it" problem anymore (MIT/GPL/AGPL all permit
commercial use fine — see below); it's a "this stops being a simple,
single-license MIT project, and instead becomes a GPL-encumbered one"
problem. Still worth fixing for the same reason it was worth fixing
before: a clean, single, unambiguous license is easier for anyone —
hobbyist or business — to actually trust and adopt without needing a
lawyer to untangle a mixed-license distribution.

**One clarification worth stating plainly, since it came up in
discussion:** none of this — GPL, AGPL, or MIT — has ever meant "no
commercial use." GPL/AGPL permit commercial use freely; what they require
is that *if you distribute or modify-and-host* the software, you keep the
combined result open under the same terms. The reason `piper-tts`'s GPL
license was worth fixing was never about blocking businesses from using
ConvoBox — it's about keeping ConvoBox itself a clean, single-license MIT
project rather than an accidentally-GPL one.

## Methodology

Two tiers of confidence:
- **Verified directly from installed package metadata** (`dist-info`
  `METADATA`/`LICENSE`/`COPYING` files in this repo's own `.venv`) —
  highest confidence, not dependent on any external source being current
  or accurate.
- **Verified via web search** — current as of 2026-07-10, sourced, but not
  independently adversarially re-checked the way the earlier licensing
  research was.

## The blocker

**`piper-tts` (PyPI package `piper-tts`, version 1.4.2, home page
`github.com/OHF-Voice/piper1-gpl`) is GPL-3.0-or-later.** Verified
directly: the package's own `METADATA` states `License: GPL-3.0-or-later`,
and its bundled `licenses/COPYING` file is the actual, complete GNU GPLv3
text (not a summary or classifier artifact — the literal license). It
also bundles a compiled `espeak-ng` binary (`espeakbridge.so`) in-process,
itself also GPL-licensed, reinforcing this is thorough, not a labeling
mistake.

**Why this matters for ConvoBox specifically:** `src/convobox/tts/piper.py`
does `from piper import PiperVoice` — a direct Python import, calling
Piper's API in-process, in the same address space and process as the rest
of ConvoBox. This is the kind of tight/"intimate" linking that GPL's own
interpretation (and mainstream legal opinion on Python dependencies)
treats as forming a single combined/derivative work, not two separate
programs merely communicating. GPL requires that combined work, when
distributed, be licensed under GPL-3.0-compatible terms to recipients.
**As currently built, if ConvoBox is distributed with `piper-tts` as a
dependency, the whole combined work is GPL-encumbered — the repo's `/LICENSE`
saying MIT would no longer accurately describe what recipients actually
receive.** That's the problem worth fixing, independent of any
commercial-licensing question.

This is a recent development, not a longstanding fact about Piper:
verified via web search — Piper was originally MIT-licensed
(`rhasspy/piper`), but that repository was **archived October 2025**;
active development moved to `OHF-Voice/piper1-gpl`, GPL-3.0, under the
Open Home Foundation. The `piper-tts` PyPI package ConvoBox depends on
tracks the new, GPL-3.0 fork — confirmed directly from the installed
package's own home-page metadata pointing at `piper1-gpl`.

## A second, independent problem: the specific voice file in use

Separate from the code question: `en_US-lessac-medium` — the exact voice
ConvoBox already downloads and uses in `scripts/roundtrip_smoketest.py`
and `scripts/spike_smoketest.py` — was flagged in search results as
potentially trained using the "lessac" voice, which may carry a
**"Blizzard" dataset-derived license** (Blizzard Challenge datasets have
historically been research/non-commercial-oriented). This is **not
independently confirmed** — it needs a direct check of that voice's own
`MODEL_CARD` (each Piper voice ships one; not yet pulled and read as part
of this audit) before any conclusion. The point to take away: **swapping
the TTS engine's code license does not automatically fix the voice
model's license** — they're independent questions, and whichever engine
ends up in use, its specific voice/model files need their own check.

## What's confirmed clean

| Dependency | License | Confidence |
|---|---|---|
| faster-whisper / ctranslate2 (code) | MIT | Verified directly from installed package metadata |
| Whisper model weights (OpenAI) | MIT | Verified via web search against OpenAI's own repo |
| silero-vad (code) | MIT | Verified directly from the actual bundled `LICENSE` file text |
| silero-vad (model weights) | Not separately verified | Code confirmed MIT; model weights not independently checked in this pass |
| torch | Apache-2.0 / BSD / MIT (compound, no copyleft component) | Verified directly from installed package metadata |
| onnxruntime | MIT | Verified directly from installed package metadata |
| httpx, httpx-sse, pydantic, pyyaml, sounddevice, numpy | MIT / BSD (various) | Verified directly from installed package metadata |
| **Kokoro-82M** (candidate replacement, not currently used) | Apache 2.0, code AND model weights | Verified via web search against the model's own Hugging Face page |
| aec-audio-processing (optional `[aec]` extra, added 2026-07-11) | BSD-3-Clause | Verified directly from the bundled `dist-info/licenses/LICENSE` (the wheel's METADATA declares no license field — the LICENSE file is the authority); wraps Google's WebRTC audio processing module, itself BSD-3 |

## Recommended fix

**Swap the default/primary TTS engine from Piper to Kokoro.** This isn't
a new decision forced by this finding — `docs/ARCHITECTURE.md`'s "Component
software" section already listed Kokoro as an undecided alternative to
Piper ("not yet finalized"). This audit resolves that choice in the
licensing-safe direction: Kokoro-82M is Apache 2.0 for both code and
model weights, genuinely permissive, no GPL entanglement.

**Not done in this pass, deliberately** (matches how the OpenCode API
finding was handled — document precisely, don't touch code without a
green light):
1. Implement `KokoroTTSEngine` (subclass of `TTSEngine`, same shape as
   `PiperTTSEngine` — see `.tours/03-extension-points-modularity.tour`'s
   checklist for adding a new TTS engine).
2. Remove `PiperTTSEngine` from the codebase rather than keep it as an
   opt-in — with no separate tier system anymore (everything is just MIT,
   free for everyone), keeping a GPL'd engine around at all, even
   optional, means "is this specific ConvoBox install still cleanly MIT?"
   depends on which engine a given user enabled. Simpler and more honest
   to just not ship a GPL dependency at all.
3. Pull and read Kokoro's actual voice/model files' individual licenses
   the same way this audit should still do for Piper's lessac voice —
   don't assume "Apache 2.0 model" means every individual voice file
   shipped with it carries identical terms without checking.
4. Re-run this same audit methodology (installed-package metadata first,
   targeted web search for anything not resolvable locally) after any
   dependency changes — this is exactly the kind of check that's cheap to
   automate and easy to silently regress on (e.g. a routine `uv sync`
   pulling in a new transitive GPL dependency without anyone noticing).

## Other things worth double-checking later, not yet done

- `huggingface-hub`/`tokenizers` (Apache 2.0, confirmed) are only used to
  *download* Whisper model files at runtime — worth confirming no
  Hugging-Face-hosted mirror of the Whisper weights ConvoBox actually
  pulls from has stricter terms than OpenAI's own MIT release (unlikely,
  but this audit didn't independently check the specific HF repo
  `Systran/faster-whisper-base` used in `roundtrip_smoketest.py` against
  OpenAI's original license terms).
- This audit covered the dependencies declared in `pyproject.toml` plus
  their direct model-weight questions. It did not do a full transitive
  audit of every indirect dependency in `uv.lock` (74 packages) — the ones
  checked were the ones with a plausible path to bundling
  restrictively-licensed code or models (audio/ML libraries), not every
  transitive package (e.g. `certifi`, `click`, `jinja2` were not
  individually re-verified here, though `pip`/`uv` metadata make it easy
  to check any specific one the same way this audit did).
