# ASCII Voice-Buddy Art — A Superfan's Recognition Audit

The current system picks 1 of 8 skeletons, recolors 5 palette slots, and swaps an eye + mouth glyph. That's a costume change, not a character. Finn and Mordecai both land on `humanoid_tall` in a blue shirt. Bender and BMO both land on `robot_boxy` in their brand color. You can't tell them apart at 14x29. Here's what it would take to actually make these icons read.

## 1. Recognition checklist — 10 iconic characters, minimum viable silhouette

- **Finn the Human** — White bear-eared hood (two short stubby ears on top of the head, NOT pointed), pale skin strip visible under the hood brim, two dot eyes, light cyan tee, dark navy shorts, bone-white skin at arms + between shirt/shorts, green backpack strap optional but iconic. Critical tell: the ear stubs. Without them he's just a kid in a hat.
- **Jake the Dog** — Golden-yellow, stretched cylinder body (NOT quadruped chibi), two floppy dangling ears (drop down past the face, not pointing up), tiny dot eyes close together, huge wide grin. Jake is fur+smile, no outfit.
- **BMO** — Pale mint-green rectangular body, WIDER than tall, face screen is a darker recessed rectangle containing eyes+mouth, four antenna-ish feet/stubs, ONE visible D-pad-shape on the body front below the screen. The body-as-face layout is the whole joke.
- **Ice King** — Long rectangular white beard that reaches below the chest (this is 50% of the silhouette), three-pronged gold crown with three visible spikes, pale ice-blue skin, pointy long nose sticking out of the beard, blue robe. No beard = no Ice King.
- **Bender** — Silver-gray segmented trapezoidal head (wider at top, narrower at mouth), single curved antenna, two big round dot eyes close together, slot-grill mouth, chest-door rectangle, cylindrical body narrower than the head. The antenna is non-negotiable.
- **Leela** — Single huge centered eye (not two), purple ponytail trailing down the side, yellow-ish tank top, white pants, one-eye-takes-up-40%-of-face. The cyclops read is everything.
- **Hypnotoad** — Wide squat green toad silhouette, two MASSIVE eyes each filling most of the head with concentric spiral rings (red/yellow alternating), no body detail needed. Just eyes + green.
- **Mordecai** — Tall blue jay. Cyan-blue body, white belly stripe, BLACK mask-stripe across the eyes (this is his Zorro tell), short pointed beak, tall thin build. Without the black eye-mask he's a generic blue bird.
- **Rigby** — Short brown raccoon, striped ringed tail curling behind, pointed muzzle, dark-brown mask around the eyes. Compact, hunched. Silhouette is brown blob + curly tail.
- **Rick Sanchez** — Cyan spike-crest hair (three distinct up-spikes), unibrow, thin long face, white lab coat, drool flecks at mouth corner optional but iconic. The three hair spikes are the silhouette.

## 2. What the current system gets wrong (3 examples)

- **Finn on `humanoid_tall`** renders a solid color block of hair, then a face, then a blue shirt. No hood shape, no ears, no white skin strip between shirt/shorts. He reads as "generic kid with a white beanie." The `hair` slot paints a rectangle where the hood should have *shape*.
- **Bender on `robot_boxy`** fills a rectangular body in silver, no antenna (template has `accent ▄ █` up top but it's a tiny stub, not a curved wire), head and body both rectangles of similar width so you lose the trapezoidal head tell. He becomes "gray refrigerator with eyes."
- **Ice King on `mystical_cloaked`** gets a hood and a robe, but the template has no beard slot. The single most iconic thing about him — the waist-length rectangular beard — does not exist. Palette can't save this. He ends up reading as generic wizard #4.

## 3. Accessory slot ranking — if we get exactly 5 slots

1. **Headwear/hood** (Finn's bear-hood, Ice King's crown, Rick's hair spikes, Jake's floppy ears, Princess Bubblegum's crown, Bender's antenna, Mordecai's head-tuft). Single biggest recognition payoff — the silhouette above the face is what the eye locks onto first.
2. **Facial hair / beard** (Ice King, Hank Hill stubble, Pops, the Wizard, Hermes). Totally absent today and it's the difference between "old guy" and "THAT old guy."
3. **Eye override — size/count/spiral** (Leela's one eye, Hypnotoad's spirals, Kyubey-style creatures, Bender's close-set dots vs BMO's wide-set). This is more than a glyph swap; we need to override eye *region size* and *count*.
4. **Body-front motif** (BMO's D-pad + screen, Bender's chest door, Finn's backpack strap, Benson's body-IS-face gumball machine). One accent shape on the torso does gigantic work.
5. **Tail/trailing element** (Rigby's ringed tail, Marceline's floating hair, Jake's stretchy limb, Lumpy Space Princess's star). Breaks the standing-rectangle silhouette and tells you this isn't a generic humanoid.

Hat beats glasses beats weapon. Weapon/prop (Finn's sword, Rick's portal gun) is slot 6 if we ever get it.

## 4. Signature shapes → slot translation

- **Finn's bear-hood**: headwear slot with "hood_with_ears" variant — two 2-cell-wide ear stubs flanking a rounded dome, plus a brim that casts one cell of shadow over the top of the eye row.
- **Bender's antenna**: headwear slot with "single_curved_wire" — a 1-cell column rising 2-3 cells with a tiny ball on top, OFFSET from center by one cell so it reads as bent.
- **Hypnotoad's eyes**: eye override with "oversized_spiral" — replaces the normal 1-glyph eye region with a 3x3 concentric pattern `◉◎◉` middle row, ring above and below.
- **Ice King's beard**: facial-hair slot with "long_rectangular" — occupies rows 6-10 below the mouth, wider at top than bottom, in the hair color. Needs to push the body down 2 rows.
- **Leela's single eye**: eye override with "single_centered" — one eye glyph at 3-cell width in the dead center of the face row, with a pupil dot inside.
- **Mordecai's black mask stripe**: accessory slot as a horizontal `shadow`-colored band spanning the eye row edge-to-edge.

The pattern: slots should be able to *override* regions of the skeleton, not just decorate them. A beard occupies rows the body currently owns. A single eye overrides the two-eye region. Purely additive accessories (crown on top, belt on waist) cover maybe 30% of iconic characters; the other 70% need regional override.

## 5. Color as identity — where it works, where it fails

**Color alone carries:** BMO (mint green + pale), Hypnotoad (radioactive green), Bender (silver-gray), Marceline (gray-blue skin), Beemo's siblings by tint variation, Gumball (literal blue cat). If the silhouette is roughly right, color seals the deal.

**Color CAN'T save it:** Finn (every kid-in-blue is Finn-colored — the hood does the work), Rick (lab coat white is 10,000 characters — the hair spikes do the work), Ice King (blue skin alone = Sadness or Frieza — the beard does the work), Mordecai vs any blue bird (the black eye-mask, not the blue, is the tell).

Rule of thumb: **color is necessary but never sufficient.** If two characters would share a silhouette, color disambiguates. If they'd share a color, silhouette disambiguates. The current system is doing 100% color, 0% silhouette disambiguation, which is why Finn and Mordecai are indistinguishable.

## 6. Edge cases — the blob problem

**Lumpy Space Princess** — bumpy purple cloud with a yellow star on its forehead. Needs a dedicated `blob_lumpy` skeleton (irregular bumpy outline, not a clean rectangle). Can't retrofit onto `creature_small`.

**Brain Slugs** — tiny one-eyed blob on top of someone's head. This is an *accessory* on another skeleton, not its own character skeleton.

**Muscle Man, Hi Five Ghost, Pops** (Regular Show) — Hi Five Ghost is literally a white hand with a face. He needs a `hand_shaped` skeleton or a heavily modified `ghost_floating`. Muscle Man's whole joke is his body shape (green, beefy, shirtless) — possible on `humanoid_stocky` with a green skin palette and a shadow belly-line accent.

**Talking food** (Peppermint Butler, Cinnamon Bun, Earl of Lemongrass) — these have a defining primary shape (cane, spiral bun, lemon) that IS the head. Needs a `food_shaped` skeleton family, OR a headwear slot so dominant it overrides the head entirely. I'd add one `amorphous_blob` skeleton with aggressive accessory slotting rather than N food-specific templates.

**Recommendation:** add `blob_amorphous` (irregular outline, no clean geometry) and `hand_creature` (for Hi Five Ghost, Thing, Rayman hands). That gets you to 10 skeletons covering ~90% of what fans will train.

## 7. Animation payoff — micro-movements from 3 frames

We already have idle / blink / talking. Cheap wins:

- **Hypnotoad eye pulse**: alternate the spiral glyph between `◉` and `◎` on idle/idle_alt. Done, no new slots needed.
- **Marceline hair drift**: for `ghost_floating` or floating-capable skeletons, offset the "hair" row by 1 cell left/right between idle/idle_alt. Sells the floating.
- **Ice King angry twitch**: eye glyph variant `●` / `◣` alternating — anger variant for when mood shifts negative.
- **Bender cigar/smoke puff**: a single `~` or `˚` glyph above the mouth on talking frame. One-cell addition, reads as smoking immediately.
- **Finn hood ear wiggle**: ear-stub glyphs shift between `▄▄` and `▀▀` across frames. Free if we add the hood slot.
- **Rigby tail curl**: tail-slot glyph rotates between `~`, `∿`, `˜`. One-cell-per-frame, huge personality.

The unlock is mood-aware frames. If PersonalityEngine's 6 moods could each pick a frame variant (grumpy Bender = slit eyes, smug Rick = smirk mouth), the same 3-frame system becomes ~18 character-states for free.

## 8. What a fan would actually test — 5 characters, pass/fail

1. **Finn** — Pass: across-the-room 2-second read as "that's Finn." Fail: "that's a kid in a white beanie." Test: hood with ears present, white-skin band between shirt and shorts visible, shirt is cyan not navy.
2. **BMO** — Pass: recognizable as "the living Game Boy." Fail: "that's a green square." Test: body wider than tall, face region is a recessed darker rectangle, D-pad shape visible on body-front, 4 stubby feet not 2 legs.
3. **Ice King** — Pass: silhouette alone (b/w, no color) reads as Ice King. Fail: "that's a wizard." Test: beard reaches below chest, 3-prong crown, pointy long nose visible through the beard gap.
4. **Bender** — Pass: recognizable even in a non-gray palette swap (the silhouette should survive recoloring). Fail: "that's a fridge with eyes." Test: trapezoidal head wider than body, single curved antenna present and OFF-CENTER, chest-door rectangle visible.
5. **Hypnotoad** — Pass: 2-second read with no context, from a thumbnail. Fail: "that's a green frog." Test: eyes occupy >50% of the face region, visible concentric ring pattern (not a flat glyph), squat wider-than-tall body.

**The meta-test**: show 5 trained voices side-by-side in a 150-pixel-wide Textual grid to someone who's watched these shows. If they can name 4 of 5 without being told which show they're from, we've shipped. If they name 1 of 5 and ask "wait which blue guy is this" — we're still doing costume-change, not character.

---

**TL;DR for the implementer:** the fix is adding regional-override slots (headwear, beard, eye-region, body-motif, tail) to the skeleton format so a single skeleton can render 5 different characters by occupying different regions. Color is second-order. The current 8 skeletons are structurally fine; they just don't have enough *addressable zones* for the classifier to target the signature shapes that make a character read.
