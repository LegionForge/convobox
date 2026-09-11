---
title: R&D survey of open ROADMAP.md items -- session persistence turns out to be a real, code-verified gap (SQLite exists but is entirely --web-coupled), plus an external landscape scan for tone-of-voice/prosody
status: validated-live
date: 2026-09-10
project: ConvoBox (github.com/LegionForge/convobox)
versions: ConvoBox main @ 74bac48
evidence:
  - Direct code read of scripts/run_convobox.py's web-startup block and src/convobox/web/history.py -- confirmed HistoryDB is only ever constructed inside the `if config.web.enabled` branch
  - grep across src/convobox/orchestrator/ and scripts/run_convobox.py for any non-web history/persistence wiring -- none found
  - Web search for the current (2026) local speech-emotion/prosody model landscape
provenance:
  authors:
    - JP Cruz <jp@legionforge.org> (asked to survey ROADMAP.md for R&D-worthy items, no implementation)
    - Claude Code (Anthropic claude-sonnet-5) -- survey, code verification, writing
  org: https://legionforge.org
  created: 2026-09-10T00:00:00Z
  revised: 2026-09-10T00:00:00Z
license: CC BY 4.0 (intent; repo code MIT)
---

# R&D survey: ROADMAP.md's open items, post-0.5-prep

With every open PR merged and the 0.5.0 CHANGELOG drafted, JP asked what's
worth spending R&D time on next. Surveyed `docs/ROADMAP.md`'s Near/Mid/
Long-term sections rather than guessing; verified the ones that looked
actionable against the real code instead of trusting the roadmap's own
framing (which turned out to matter -- see below).

## Finding: "Session persistence" is not actually a tiered, decided
feature in progress -- it's SQLite, entirely coupled to `--web`

`docs/ROADMAP.md` describes this as **"decided: configurable, tiered"**:
*"nothing -> flat files -> sqlite -> postgres/pgvector, user-configurable,
with ConvoBox suggesting optional installs as needs grow."* Read that as
settled direction and checked what's actually built.

**What's real:** `src/convobox/web/history.py`'s `HistoryDB` is a
genuine, working SQLite-backed event store -- indexed schema, real
queries, already shipped and tested. But it's constructed in exactly one
place, `scripts/run_convobox.py`, entirely inside the
`if config.web.enabled:` startup branch:

```python
web_app_history = (
    HistoryDB(Path(config.web.history_dir) / "events.db")
    if config.web.history_tracking_enabled
    else HistoryDB(Path(":memory:"))
)
web_forwarder = WebEventForwarder(
    new_session_id(),
    history=web_app_history if config.web.history_tracking_enabled else None,
    broadcaster=web_broadcaster,
)
```

Grepped the rest of the codebase (`src/convobox/orchestrator/`,
`scripts/run_convobox.py` outside that block) for any other persistence
wiring -- there is none. **A pure voice session (`convobox`, no `--web`)
has zero conversation persistence, unconditionally, regardless of any
config value.** The roadmap's "tiered, user-configurable" framing implies
this is a spectrum the user picks a point on; what's actually shipped is
a binary "on, but only as a side effect of also running the web server"
or "off." Two of the four described tiers (flat files, postgres) don't
exist in any form; "sqlite" exists but isn't reachable independently.

This isn't a criticism of the SQLite work itself -- `HistoryDB` is solid,
and building it for the web UI first was the right incremental move
(recorded in its own PR, #366-era work). It's that the roadmap entry
reads like a decision that's been executed on, when what's actually true
is "the web UI's needs happened to require something adjacent to tier 3,
and that's the only tier that exists, gated behind an unrelated flag."

**What closing this gap for real would take** (scoping only, not built):
- Decouple `HistoryDB`/`WebEventForwarder`-equivalent event capture from
  `web.enabled` -- move it into `Orchestrator`'s own setup, gated by its
  own config (not reusing `web.history_tracking_enabled`, which is a
  web-UI-specific toggle name for what would become a core feature).
- The "flat files" tier doesn't need new code the same way SQLite did --
  it's a strictly simpler serialization of the same `BackendEvent`
  stream `HistoryDB` already consumes; worth deciding whether it's worth
  building at all now that SQLite (stdlib, no extra) already covers the
  "no extra dependency" goal flat files were presumably meant to serve.
- Real product question, not an engineering one: does a voice-only user
  actually want their conversation history persisted by default, given
  this project's own stated privacy posture elsewhere (the declined-for-
  now redaction proposal, the deliberate non-cookie auth-token design)?
  That's JP's call, not something to infer from the roadmap text.

**Recommendation:** worth a real decision from JP on whether "session
persistence, independent of `--web`" is wanted before any code gets
written -- this is app-behavior-affecting (what gets written to disk by
default), not a safe default-on addition the way the doctor command or
the git-repo warning were.

## Landscape scan: tone-of-voice / prosody (still "proposed, not yet
decided" -- JP hasn't greenlit this as R&D, so scoped only as a landscape
check, not a prototype)

The roadmap's own framing already sets the right bar: arousal/valence
first, read-only milestone, "prove it's real before it affects behavior."
A quick scan of what's actually available locally in 2026, treated with
the same skepticism this project already applies to vendor/marketing
claims (see the declined FunASR candidate in this same roadmap file):

- **DistilHuBERT** comes up as the most-cited lightweight option for this
  exact task -- a distilled HuBERT (2-layer transformer encoder) fine-
  tuned for speech emotion recognition. One source's headline number
  (70.64% accuracy, "0.02 MB" model size) doesn't survive a sanity check
  -- a working transformer encoder cannot plausibly be 0.02 MB regardless
  of distillation; that's very likely a units error or a mis-cited head-
  only figure in the source, not a real end-to-end model size. Treat as
  "worth investigating," not "verified," same tier this project's own
  DEPENDENCY_LICENSE_AUDIT.md-style skepticism already holds numbers
  from a single blog source to.
- **SpeechBrain** (PyTorch-based speech toolkit) is the more credible,
  established framework mentioned alongside it -- has real maintained
  emotion-recognition recipes, would be the safer integration point if
  this gets prototyped, rather than a single distilled checkpoint from
  an unverified source.
- General finding across sources: fusing voice-embedding prosody with
  transcript-level text sentiment (i.e., using both what was said and
  how) is reported as the biggest recent accuracy gain -- relevant if
  this ever gets built, since ConvoBox already has both signals
  available (STT transcript + raw audio) at the exact point this would
  hook in.

**Not recommending a prototype yet** -- this item is explicitly "not yet
decided" in the roadmap's own words, unlike session persistence above
("decided"). Scoping further (picking a specific model, building the
read-only milestone) is real implementation-adjacent work that should
wait for JP's go-ahead, same discipline the redaction proposal is
already being held to.

## Other roadmap items checked, not pursued

- **Redaction for stored transcripts/logs**: JP's own roadmap note says
  "not immediately actionable... not scoping it yet" -- respecting that,
  no work done here. (Ironically, now more relevant than when written,
  given the session-persistence finding above confirms real transcripts
  already do get persisted whenever `--web` is on.)
- **Safety tiers for destructive actions**: roadmap section reads as a
  design sketch, but a spoken-approval-phrase channel, `kill_phrase`,
  and per-backend approval wiring are all extensively shipped already
  (this whole project's PR history) -- the roadmap entry itself looks
  stale relative to what's built, not a real open gap. Didn't do a full
  audit to confirm it's fully stale; flagging as a documentation-
  accuracy item, not an R&D target.
- **Wake word**: explicitly deferred past 0.5 by JP's own 2026-07-12
  decision -- correctly out of scope right now.
- **Alternative STT engines**: no new candidate has emerged since the
  2026-09-03 Parakeet TDT pass (evaluated, mixed result, not a clear
  win) and the earlier FunASR pass (declined, marketing-only evidence).
  Nothing new to research here without a new candidate worth the same
  scrutiny.

Sources for the prosody landscape scan:
- [Best Local TTS Models in 2026](https://localclaw.io/blog/local-tts-guide-2026)
- [How to Implement Audio Emotion Detection with AI: 2026 Playbook](https://www.forasoft.com/blog/article/audio-emotion-detection-system-using-ai)
- [speech-emotion-recognition · GitHub Topics](https://github.com/topics/speech-emotion-recognition)
- [Emotion Detection in Speech Using Lightweight and Transformer-Based Models](https://arxiv.org/html/2511.00402v1)
- [Emotion as a Distribution: Joint Valence–Arousal Probability Learning](https://arxiv.org/html/2609.05755)
