---
title: A hotwords list can come back out as the transcript, so it must never contain a spoken secret
status: diagnosed
date: 2026-11-21
project: ConvoBox (github.com/LegionForge/convobox)
versions: faster-whisper 1.2.1; ctranslate2 4.8.1 (CPU, float32); Whisper model "base"; numpy 2.5.1; ConvoBox 0.5.1 + main @ 2f83d0f; Windows 11
evidence:
  - live UAT log 2026-11-21 (convobox-tui.log, 21:54:04, 22:06:24, 22:08:47)
  - offline reproduction on synthetic audio, scripts kept outside the repo (summary in "Evidence")
  - PR #452 (approve-side guard, stacked on #447), PR #453 (Settings help text)
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (operator; live testing and the safety concern)
    - Claude Code (Anthropic claude-sonnet-5-5) — investigation, offline reproduction, implementation, writing
  org: https://legionforge.org
  created: 2026-11-21T23:58:05-06:00
  revised: 2026-11-21T23:58:05-06:00
license: CC BY 4.0 (intent; repo code MIT)
---

# A hotwords list can come back out as the transcript, so it must never contain a spoken secret

**Context for outsiders.** ConvoBox is a voice front end for coding agents. A spoken
"approval phrase" gates tool calls, and spoken "stop phrases" abort the agent.
Speech-to-text is faster-whisper, which accepts a `hotwords` string that biases
recognition toward chosen words. The finding applies to any system that both feeds a
prompt or hotword list to Whisper and acts on the resulting text.

## Problem

Operators were advised to put the approval phrase and the stop phrases into
`stt.hotwords`, because short unusual words are easy for Whisper to mishear. In live
testing the recognizer returned the hotwords themselves as transcripts when nobody had
said them. If a transcript recited the whole list while an approval was pending, and the
approval phrase sat in that list as consecutive words, the approval check would have
accepted it from noise.

## Evidence

Live, 2026-11-21, `convobox-tui.log` (Brio mic, laptop speakers, claude-code backend):

- `21:54:04 approval remains pending while the operator reviews/discusses: 'stop brake eject mayday listening'`
- `22:06:24 ... 'stop brake eject'`
- `22:08:47 transcript='brake brake brake ... (14x)' lang=en (0.39) [HARD STOP]`

The first is the first five words of the configured `hotwords`
(`stop brake eject mayday listening resume alpha bravo delta halt abort`), verbatim.

Offline reproduction on **synthetic** audio (speech-band noise gated into syllable-like
bursts; no real speech), same STT config, CPU:

| Condition | Trials | Output contained hotword text | Approval words adjacent in output | Detector said APPROVE |
|---|---|---|---|---|
| Current list (11 words, approval words included) | 192 | 136 | 0 | 0 |
| Same list minus the approval words | 192 | 143 | 0 | 0 |
| Three random shuffles, approval words scattered | 288 (96 each) | 29 / 68 / 58 | 0 | 0 |
| No hotwords (7 clip types x 6) | 42 | 0 (hallucinated other text) | n/a | n/a |

Without hotwords the same audio is still hallucinated, as `"I'm sorry, I'm sorry, ..."`
or `'You'`. So the hallucination does not come from the hotwords; the hotwords decide
**what** is hallucinated. Longest recitation with the current list was
`stop brake eject mayday listening resume`, one word short of the approval phrase. All
three shuffles also produced a stop phrase as hallucinated text (`stop stop stop` in one,
`brake brake brake ...` loops in the others), which is a spurious stop (the safe
direction). The approval words did still appear singly in 14 and 7 of the outputs of two
shuffles, never next to each other.

## Mechanism

Diagnosed, not proven at the decoder level. faster-whisper puts `hotwords` into the
decoder prompt. On short or noise-like audio Whisper has little acoustic evidence and
falls back to prompt-like text, either repeating a single token or reciting the prompt.
The approval detector (`ConfirmwordDetector.check`) accepts the phrase as consecutive
words anywhere in a transcript, so a recited list containing those three words in order
would match. The offline recitations stopped around five to six words, which is why no
approval occurred; we do not know why.

Ruled out: the overlap/echo gate (#447) as a defence. It only looks at whether ConvoBox
audio was playing, so a hallucination in a quiet room passes it.

## What transfers

- **Never put a spoken secret or an authorizing phrase in a recognition prompt or
  hotword list in order.** (diagnosed)
- **A hallucinated transcript is a real input to your command layer.** Gate the
  authorizing path on content-independent plausibility: words per second of audio, and
  verbatim runs of your own prompt. (implemented in #452; unit-tested, not live-verified)
- **Keep the safe-direction path (stop) permissive.** Do not apply plausibility guards to
  it; a spurious stop costs a repeat, a missed stop costs control. (design rule)
- **Shuffling a hotword list reduces recitation risk but does not remove it, and it does
  not stop single-word loops.** (offline only, 3 shuffles)
- **Not established:** real-world frequency (three live occurrences in about two
  sessions of use; the synthetic 70% is adversarial and not a rate), and whether removing
  the approval words from the list worsens recognition of the phrase (not measured).
