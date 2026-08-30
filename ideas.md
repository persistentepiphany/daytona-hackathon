# Snapshot Landing Page — Design Direction

## Three possible directions

### 1. Forensic Modernism
**Very Brief Intro:** An evidence instrument rendered as a living document: large technical statements, gridlines, sharp data states, and deliberate negative space. It feels rigorous rather than futuristic.

**Probability:** 0.03

### 2. Archive in Motion
**Very Brief Intro:** A monochrome research archive where pages, stamps, and field notes become a quiet animated sequence. It favours historical texture and editorial pacing.

**Probability:** 0.08

### 3. Cold Signal
**Very Brief Intro:** A dark, restrained systems interface with a narrow phosphor-green signal and low-light instrumentation. Motion reads as execution telemetry, not visual spectacle.

**Probability:** 0.06

## Chosen approach — Forensic Modernism

### Design Movement
Contemporary Swiss editorial design crossed with laboratory documentation systems. It avoids glossy SaaS tropes and turns the scientific process into the interface.

### Core Principles
1. **Show the proof chain, not the platform chrome.** The first viewport must state the product thesis and stage its process in one read.
2. **Use reduction as emphasis.** Few objects, direct copy, generous whitespace, and limited colour make each status change meaningful.
3. **Make motion explanatory.** Elements move only when they freeze, branch, resolve, or verify the underlying process.
4. **Keep the surface materially credible.** Fine rules, document grain, and small provenance labels reference research practice without becoming nostalgic.

### Color Philosophy
Warm document-white creates the baseline of a paper record; almost-black ink provides authority and contrast. A single oxidised cyan signal marks active states, successful verification, and the immutable system anchor. There are no gradients, violet accents, or decorative glows.

### Layout Paradigm
The page operates as a vertically unfolding **evidence ledger**, not a series of centered marketing blocks. The hero establishes a thesis on the left while an animated execution line runs on the right. Subsequent modules alternate between full-bleed statements and edge-aligned evidence strips.

### Signature Elements
- A thin live **lineage thread** that freezes, branches, and converges as the story progresses.
- Monospaced **evidence labels** that identify stages, hashes, and states without pretending to be a product dashboard.
- Single-stroke **registration marks** and quiet grid rules that give the page a documentary frame.

### Interaction Philosophy
Scrolling advances the proof chain. Hovering over a step reveals only the minimum annotation required to understand its control; there are no ornamental controls, CTA clusters, or fake product interactions.

### Animation
The initial sequence lasts about five seconds: claim types in, an environment record locks, three parallel evidence lines launch, then a verdict resolves. Scroll-triggered sections use brief 180–280ms opacity and transform transitions with staggered labels. The lineage thread draws through the process with SVG stroke animation. All non-essential motion is suppressed under `prefers-reduced-motion`.

### Typography System
**Barlow Condensed** is used for large, compressed thesis statements and numbered stages; **IBM Plex Mono** carries IDs, state labels, and evidence annotations; **Manrope** handles the brief explanatory copy. No Inter, italic editorial headings, or generic startup language.

### Brand Essence
**Snapshot turns published claims into replayable evidence for teams who build where trust matters.** Precise, skeptical, alive.

### Brand Voice
Headlines are declarative and specific; labels read like an audit trail; microcopy avoids persuasion.

> “A paper is not proof. Run it.”

> “Freeze the state. Test the claim. Keep the evidence.”

### Wordmark & Logo
The wordmark uses a compact uppercase `SNAPSHOT` label paired with a custom split-square mark: one solid black frame, one offset cyan “captured” plane. The mark reads as both a snapshot and a frozen experiment boundary.

### Signature Brand Color
**Verdict Cyan — `#0C8AA5`**

## Style Decisions

- The user chose a **straight-to-the-point, minimalistic** page. The page will keep one product argument per visual state and will not use conventional calls to action.
- Motion supports comprehension rather than polish: no looping gradients, floating ornaments, bouncy UI, or unnecessary micro-interactions.
