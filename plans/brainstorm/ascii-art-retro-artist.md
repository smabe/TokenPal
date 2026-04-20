# Retro Art Director: Making Voice Buddies Distinctive

Reviewed `tokenpal/ui/ascii_skeletons.py`. The bones are sound — 14x29, slot-substitution, half-block silhouettes. The problem is expressive ceiling: with 8 skeletons x 5-color palette x 2 glyph slots, two different voices land on the same silhouette and read as "same character, different shirt." Below is the prioritized plan to fix that without ever letting the LLM draw.

Guiding principle from BBS/demoscene tradition: **silhouette first, color second, texture third, face last.** If two characters have the same outline, no palette swap will save them. So the biggest wins are in adding controlled silhouette variance, not more colors.

## Priority 1 — Accessory layer (overlay glyphs at named anchors)

This is the highest ROI change and is low-risk because accessories are single-cell glyphs at fixed coordinates. Define per-skeleton **anchor points** (row, col, width) and let the LLM return a list of accessories by slot name, each a small enum:

- `headtop` (above row 0): `♛` crown, `▲` hat peak, `◉` halo (use accent color), `⚙` antenna, `✧` sparkle, `☾` moon, `⌬` gear, empty
- `headside_l` / `headside_r` (rows 1-3, outer columns): `▌▐` bold ear, `⟨⟩` hoop earrings, `⟢⟣` feather, empty
- `foreheadband` (row 2, centered, 3 cells): `━━━` band, `✦✦✦` studded, `░░░` bandana texture, empty
- `neck` (below chin row): `▬` collar, `◇◆◇` necklace, `╲╱` bowtie, empty
- `hand_l` / `hand_r` (bottom body row, outer columns): `†` staff, `⚔` sword, `⚡` wand, `♣` book, empty
- `backplane` (wing/cape row if skeleton has one): flag glyphs for cape flare

Keep face glyphs sacred — never allow accessory rows to overlap the eye/mouth rows. Anchors outside the face region guarantees no clash. **Six anchor slots with ~5 options each ≈ 15,000 combinations** before palette. That's plenty.

JSON shape stays safe:
```
"accessories": {"headtop": "crown", "neck": "bowtie", "hand_r": "wand"}
```
Render pass: after slot substitution, walk the accessory dict and splice the glyph at the anchor's `(row, col)` using the accent color. Unknown keys ignored — gemma4's favorite failure mode is inventing keys, so silent-drop beats crash.

## Priority 2 — Body variants per skeleton (cheaper than more skeletons)

Adding a 9th skeleton costs a new 14-line template + sample palette + tests. Adding a `variant` dimension to existing skeletons costs one extra row-slice per variant. Recommended:

- `humanoid_tall`: `torso_muscular` (wider row 8-11 by +2 cols), `torso_slim` (narrower by -2), `torso_robe` (replace legs row 12-13 with ▓▓ flare). Three silhouettes from one file.
- `robot_boxy`: `head_round` vs `head_boxy` (swap rows 0-7 for a round-chamfer top using `▛▜` and `▙▟`), `body_treads` vs `body_legs`.
- `creature_small`: `ears_up` vs `ears_floppy` (two variants on rows 0-1 only).

Implementation: store variants as overlay patches — `{"rows": [8, 9, 10], "content": [...]}` merged after the base template. **LLM picks skeleton + variant**; the variant enum is a closed list so it can't hallucinate new shapes. Three variants per skeleton triples silhouette count at ~10% the cost of a new skeleton.

## Priority 3 — Texture slots on outfit and hair

This is where demoscene technique pays off. Right now outfit fill is pure `▓`. Let the classifier pick a `texture` enum that swaps the fill glyph pattern:

- `solid` → `▓▓▓▓▓▓▓` (current)
- `stripes_h` → `▓▓▒▒▓▓▒▒` (horizontal bands, two-tone uses shadow color)
- `stripes_v` → alternating column columns `▓░▓░▓░`
- `checker` → `▓░▓░` with row parity flip
- `dither_up` → `▓▒░` left-to-right gradient (the best demoscene trick; reads as lighting from one side)
- `dither_down` → `░▒▓` reversed gradient
- `scales` → `▞▚▞▚` quarter-block zigzag (great for dragons, fish, armor)
- `spots` → sparse `◦` overlay on `▓` base

At 29 cells wide, **avoid patterns finer than 2-cell repeats** — anything denser reads as noise. Six texture types is the sweet spot; eight starts looking same-y. Apply to outfit primarily; hair gets a simpler `solid | streaked | spiky` enum.

## Priority 4 — Six-color palette, specifically a `highlight`

Don't do two-color gradients per slot — that doubles the palette size the LLM has to reason about and gemma4 will confuse which hex goes where. Instead, add one slot: **`highlight`** (brighter than outfit). Use it for: dither_up bright edge, scale sheen, eye sparkles, weapon gleam. Demoscene precedent: 16-color EGA palettes always reserved one "hi" slot per region (dark/mid/light triad). Our five colors collapse to a two-tone per region; adding highlight gets us to the classic three-tone without doubling LLM work.

## Priority 5 — Pose as an additive patch, not a new skeleton

Define three pose modifiers, each patches 2-4 rows on top of the base:

- `neutral` (default, no patch)
- `hand_raised`: replace row 8 rightmost 3 cells with `▄█▀` arm-up silhouette
- `lean`: shift rows 0-5 left by one column (compensating pad right)

Pose is orthogonal to variant. Pose stays constant across idle/blink/talking to preserve sync.

## Priority 6 — Braille ONLY for eye detail, never body

Braille (`⠁⠉⠛⡿⣿`) is tempting for density but **it breaks monospace alignment in many terminal fonts** and looks muddy at zoom. Exception: eyes benefit from Braille-like compound glyphs. Extend the `eye` enum: `●` (standard), `◉` (pupil), `◐` (half-closed), `⚆` (cross-eyed), `⌐` (side-glance), `✦` (sparkle), `✕` (dead), `○` (empty/shocked). Mouth likewise: `▽ ◇ ᗣ ᴗ ⌣ ═ ᗢ`. This is already a closed enum — just grow it to ~10 each and document which fit which mood.

Quarter-blocks (`▘▝▖▗▚▞`): use sparingly on silhouette edges where a sharp corner looks blocky. One good application is `winged` wingtip — `▀▚` reads as a tapered feather better than `▀█`. Jitter risk is real: terminals with non-square cell ratios will show them shifted. Keep them to silhouette edges only, never interior fill.

## Priority 7 — Fourth "reaction" frame

Yes, but keep it optional and cheap. Define `reaction` as idle + one patch: eyes go `✦`, mouth goes `ᴏ`, plus optional `!` glyph spliced at `headtop+1`. Orchestrator fires it on high-signal sense events (git push, new commit, drift nudge, rage detect). No new template — it's a runtime glyph swap on the idle frame. Keeps the three-frame animation loop untouched.

## Techniques to steal from 16colo.rs

1. **Smart edge chamfer** — `▛▜▙▟` on outside corners of boxes. Apply to `robot_boxy` head and body corners; instantly looks less "ASCII-art 101."
2. **Vertical dither gradient** on torso (`▓▓▓` row 8 → `▒▒▒` row 9 → `░░░` row 10) reads as chest-to-belly shading. Cheap, universal, works on every humanoid.
3. **Contrasting outline** — draw the silhouette edge in `shadow` color, interior in `outfit`. Most current skeletons fill edge and interior the same color. A 1-cell dark outline on `humanoid_tall` doubles the perceived detail. Implementation: split each row's first and last fill cell into a `{shadow}` tag.
4. **"Lit from upper-left" convention** — pick one light direction and enforce it in every template. Currently shading is inconsistent; unifying it makes the whole roster feel like one art style instead of eight styles.

## Summary of recommended JSON schema additions

```json
{
  "skeleton": "humanoid_tall",
  "variant": "torso_muscular",
  "pose": "hand_raised",
  "texture_outfit": "dither_up",
  "texture_hair": "spiky",
  "palette": {"hair": "#...", "skin": "#...", "outfit": "#...",
              "accent": "#...", "shadow": "#...", "highlight": "#..."},
  "eye": "◉", "mouth": "▽",
  "accessories": {"headtop": "crown", "neck": "bowtie"}
}
```

**Combinatorial uplift**: 8 skeletons × 3 variants × 3 poses × 6 textures × 5 accessory slots × ~5 options each. From ~8 distinct silhouettes today to effectively unlimited distinct portraits, with zero freeform drawing and every field a closed enum the classifier can validate.

**Implementation order**: (1) accessory layer + anchors, (2) outline/shadow unification across existing templates, (3) texture enum on outfit, (4) body variants for the three most-used skeletons (humanoid_tall, robot_boxy, creature_small), (5) highlight color, (6) pose patches, (7) reaction frame. Ship 1-3 first; they alone will triple perceived variety.
