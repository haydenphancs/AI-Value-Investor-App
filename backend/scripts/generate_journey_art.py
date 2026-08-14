"""
Stage 1 of 2 — generate the hero illustration for each Investor Journey lesson. PAID.

    generate_journey_art.py  ->  seed_journey.py (uploads + writes cards[0].imageUrl)

ONE image per lesson, shown on the FIRST card only (`type: "title"`). The completion
card no longer carries an image.

Checkpointed on a hash of the prompt: an unchanged prompt makes NO API call, so
re-running is free. Change one subject and only that lesson regenerates. NOTE that
the style block and the palette are part of the hashed prompt — editing STYLE or a
PALETTE entry deliberately re-rolls every lesson it touches, at full cost.

The art is composited by iOS onto an always-white plate (AppColors.mediaSurface) so
it looks identical in light and dark mode. That is why a PURE WHITE background is a
hard requirement and is validated locally after every generation — an off-white
render shows a visible seam inside the plate in dark mode, and you will not catch
that by eye across 27 images.

Output (full run):
    backend/data/journey_art/<slug>.art.jpg     2K master, 16:9, q95
    backend/data/journey_art/<slug>.hero.jpg    1536x864 q82 — this is what ships
    backend/data/journey_art/<slug>.manifest.json

Usage (from backend/):
    ./venv/bin/python scripts/generate_journey_art.py --style        # style bake-off, pick one
    ./venv/bin/python scripts/generate_journey_art.py                # all 27, skips up-to-date
    ./venv/bin/python scripts/generate_journey_art.py --only mr_market
    ./venv/bin/python scripts/generate_journey_art.py --only mr_market --force
"""
import hashlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # backend/
OUT = ROOT / "data/journey_art"
STYLE_OUT = OUT / "_style"
VAR_OUT = OUT / "_variants"
LESSONS_JSON = ROOT.parent / "frontend/ios/ios/Resources/Journey/journey_lessons.json"

# --- key, same hand-rolled .env parse the rest of the content pipeline uses -----
KEY = os.environ.get("GEMINI_API_KEY")
_env = ROOT / ".env"
if not KEY and _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
if not KEY and __name__ == "__main__":
    raise SystemExit("GEMINI_API_KEY not found in env or backend/.env")

MODEL = os.environ.get("JOURNEY_ART_MODEL", "gemini-3-pro-image")
THROTTLE = float(os.environ.get("JOURNEY_ART_THROTTLE", "4"))
MAX_ATTEMPTS = 4

HERO_W, HERO_H = 1536, 864          # 16:9, 1.48x the 345x180pt @3x slot
HERO_QUALITY = 82

# White-background floor. The iOS plate is #FFFFFF; anything below this shows a seam.
WHITE_MIN_CHANNEL = 248
WHITE_MAX_SPREAD = 6
WHITE_RING_PX = 8

# The model reliably returns a 250-254 "white" rather than a true 255, which reads as a
# faint rectangle inside the #FFFFFF plate. Snap anything already visually white to pure
# white in the derivative. Deliberately a GLOBAL threshold rather than a border flood
# fill: a pixel within ~9 levels of white is white wherever it sits, and the O(n) form
# costs nothing, while a flood fill is a slow pure-Python BFS over 4M pixels.
SNAP_MIN_CHANNEL = 246
SNAP_MAX_CAST = 6

# =============================================================================
# STYLE — the one creative decision. `--style` renders every candidate against two
# test subjects for a human to pick; the winner's key goes in STYLE below and is
# baked into every lesson prompt.
# =============================================================================
STYLE = os.environ.get("JOURNEY_ART_STYLE", "flat_vector")

STYLES = {
    # A — the evolution of the current direction: crisp, flat, unmistakably a
    #     financial-app asset. Reads instantly at 180pt tall.
    "flat_vector": (
        "Clean 2D vector illustration in a flat corporate editorial style. Crisp geometric "
        "vector shapes with clean hard edges, solid flat colour fills, confident even linework, "
        "a few subtle dynamic motion lines to imply movement, generous negative space. A modern "
        "financial app illustration asset. Absolutely flat — no photographic texture, no 3D "
        "rendering, no bevels, no glossy highlights, no drop shadows."
    ),
    # B — semi-dimensional premium fintech. More polished and tactile; better at
    #     structural / systems ideas, heavier at small sizes.
    "soft_depth": (
        "Modern semi-dimensional illustration with a soft isometric perspective. Rounded "
        "volumetric forms with gentle smooth gradients and soft ambient shading, matte surfaces, "
        "one delicate soft-focus contact shadow directly beneath the object only. Premium fintech "
        "product illustration — clean, calm and uncluttered. Not photorealistic, not a glossy 3D "
        "render: no reflections, no hard specular highlights, no chrome."
    ),
    # C — editorial cut-paper. Warmest and most 'story'; least like a UI asset.
    "paper_cut": (
        "Layered cut-paper collage illustration. Bold simple silhouettes cut from sheets of "
        "coloured paper, softly visible paper edges, a fine subtle grain texture on the coloured "
        "shapes only, gentle offset layering giving quiet depth. Warm editorial magazine explainer "
        "feel with a screen-print / risograph quality and slight ink overlap where two colours "
        "meet. Hand-made and tactile, never digital-slick."
    ),
}

# =============================================================================
# PALETTE — colour carries the journey LEVEL, so the art agrees with the level
# chips: foundation blue · analysis teal · strategies amber · mastery violet.
# Four lessons override their level palette because their CONTENT demands it
# (inflation and red flags read as warning; the two crypto lessons read as crypto).
# Anchored on the real brand tokens in Theme/AppTheme.swift:172-173.
# =============================================================================
PALETTES = {
    "blue": (
        "a confident royal blue #2563EB with a lighter sky blue, cool slate-grey neutrals and "
        "clean white. Calm and trustworthy."
    ),
    "teal": (
        "a deep teal #0E7490 with a brighter cyan, cool slate-grey neutrals and clean white. "
        "Analytical and precise."
    ),
    "amber": (
        "a warm amber and antique gold, with slate-grey neutrals, clean white, and one small "
        "deep-blue accent for contrast. Considered and warm."
    ),
    "violet": (
        "a deep violet and indigo with a soft lilac, slate-grey neutrals and clean white, plus "
        "one small cyan accent. Reflective and advanced."
    ),
    "crimson": (
        "a deep crimson red and warm coral, with slate-grey neutrals and clean white. Cautionary "
        "but never alarming — rich and restrained, not a bright emergency red."
    ),
    "ember": (
        "a burnt orange and warm ember gold, with slate-grey neutrals and clean white. Energetic "
        "and modern."
    ),
    "green": (
        "a fresh confident green and a deeper forest green, with slate-grey neutrals and clean "
        "white. Healthy and growing — never neon, never lime."
    ),
    # Level teal, plus ONE green reserved for the thing that survives. Green is doing
    # semantic work here, not decoration: in a lesson about what is left after every cost,
    # the remainder needs to be the one element that is a different colour from everything
    # taken away from it.
    "teal_green": (
        "a deep teal #0E7490 and a brighter cyan for the main shapes, with ONE fresh green "
        "reserved exclusively for the single small element representing what is left over at the "
        "end, plus cool slate-grey neutrals and clean white."
    ),
}

LEVEL_PALETTE = {
    "foundation": "blue",
    "analysis": "teal",
    "strategies": "amber",
    "mastery": "violet",
}

# slug -> (level, palette, subject).  Keyed on the AUTHORED slug from
# journey_lessons.json — the same key seed_journey.py feeds to uuid5 for the row id,
# so the storage path and the DB identity cannot drift. It is NOT the title-derived
# slug iOS uses for bundled assets (14 of 27 disagree).
LESSONS = {
    # ---- FOUNDATION ---------------------------------------------------------
    "compound_interest": ("foundation", "blue",
        "a small snowball at the top left rolling down a gentle slope and growing into a much "
        "larger sphere at the lower right, its path drawn as a clean curved trail that sweeps "
        "upward at the end"),
    "stock_vs_business": ("foundation", "blue",
        "a single wedge-shaped slice lifted slightly out of a simple round pie, the pie rendered "
        "as the clean geometric roofline and windows of a small office building, so one object "
        "reads as both a share and a real business"),
    "mr_market": ("foundation", "blue",
        "two identical price tags hanging side by side from a single bar, one swinging high and "
        "one swinging low, with a small pendulum arc drawn between them"),
    # NOT a balance scale. A scale weighs one thing AGAINST another — "take less of this to
    # get more of that" — and this lesson argues the opposite: the two cannot be separated
    # at all ("bigger rewards come holding hands with bigger risk", headline "Two Sides of a
    # Coin"). Interlocking links say linked. Chosen from three candidates; see VARIANTS.
    "risk_reward": ("foundation", "blue",
        "two interlocking chain links lying at a slight angle, solidly filled and clearly "
        "threaded through one another so neither can be removed without breaking the other, the "
        "upper link carrying a small upward arrow and the lower link a small downward arrow. "
        "Bold solid fills, heavy confident shapes, not thin outline linework"),
    "bitcoin_digital_gold": ("foundation", "ember",
        "a single thick round coin standing upright on its edge, its face a clean geometric "
        "circuit-board pattern of fine connected lines and nodes rather than any symbol or letter"),
    "etfs_101": ("foundation", "blue",
        "one open woven basket holding a neat cluster of many small identical geometric shapes — "
        "circles, squares and triangles — in several colours, a few resting beside it"),
    "inflation_thief": ("foundation", "crimson",
        "a stack of round coins with its top third dissolving into small drifting particles that "
        "blow away to the right, the remaining stack visibly shorter than its faint outline"),

    # ---- ANALYSIS -----------------------------------------------------------
    # NOT full circular dials with tick marks — that renders as three CLOCKS, which is what
    # the first attempt produced. Half-ring arcs read as gauges and cannot read as a clock.
    "key_statistics": ("analysis", "teal",
        "three simple half-ring arc gauges of the same size standing in a neat evenly spaced row, "
        "each drawn as a semicircular arc with a short straight needle pointing to a different "
        "position along it and a different length of the arc filled in. Half circles only — no "
        "full circular dial, no ring of tick marks, nothing that could read as a clock face"),
    # A waterfall, and the UNEVEN heights are the point. An even staircase reads as a made-up
    # decorative pattern; real financials fall in irregular steps — a small trim, then a big
    # one — so stating the four ratios explicitly is what makes it look like a real company
    # rather than a graphic. Green is reserved for the final bar: what you actually keep.
    # (Two earlier funnel/band constructions were scrapped — see VARIANTS.)
    "income_statement": ("analysis", "teal_green",
        "exactly four solid vertical bars standing side by side on one common horizontal "
        "baseline, evenly spaced, read left to right. Their heights are deliberately UNEVEN and "
        "must not form a regular staircase: the first bar is full height; the second is only "
        "slightly shorter than the first, about four fifths of its height; the third drops "
        "sharply to about half the height of the first; and the fourth is much shorter again, "
        "roughly one fifth of the first. The first three bars are teal and cyan. The FOURTH AND "
        "LAST bar is GREEN and is the only green element anywhere in the picture. A thin stepped "
        "connector line runs from the top of each bar across to the top of the next, so the eye "
        "follows the amount falling away. Exactly four bars — no more, no fewer — all complete "
        "and well inside the frame with clear white margin on every side"),
    "balance_sheet": ("analysis", "teal",
        "two neat stacks of flat rectangular blocks standing on the two pans of a level balance "
        "beam, the left stack made of solid blocks and the right of outlined ones, perfectly level"),
    # Two fixes, in order. First attempt ran the pipe off the left edge and the reservoir off
    # the right, so both ends of the pipe now have to terminate inside the frame. The catch
    # basin was then the busiest object in the whole set — an extruded trough with thick
    # walls, side blocks, a wavy surface AND coins settled in it, four competing details in
    # the bottom third at 180pt tall. Replaced with a plain bucket, and the exclusions are
    # spelled out because "simple" alone gets ignored.
    "cash_flow": ("analysis", "teal",
        "a straight horizontal cylindrical pipe running left to right across the upper part of "
        "the frame, with a flanged cap closing each end so both ends finish well inside the "
        "frame, and one simple T-shaped valve handle on a short straight stem mounted on top of "
        "the pipe left of centre — a plain T bar, not a spoked wheel. A circular opening on the "
        "UNDERSIDE of the pipe, right of centre, from which a thick smooth stream falls straight "
        "down, with two or three round coins riding the falling stream. Directly below it stands "
        "a simple open bucket: a plain flat trapezoid slightly narrower at the bottom than the "
        "top, with one straight rim line across the top. The LOWER TWO THIRDS of the bucket is "
        "FILLED with a solid block of teal liquid whose top edge is one straight horizontal "
        "line — a solid filled area of colour clearly darker than the bucket itself, definitely "
        "not a thin stripe, band or empty container. Completely flat with no extruded depth, no "
        "thick walls, no side panels or blocks beside it, no wavy or rippled surface, and no "
        "coins resting in the liquid. The pipe, the valve handle, the stream and "
        "the whole bucket are complete and fully visible, centred with generous empty white "
        "margin on all four sides and nothing touching an edge"),
    "economic_moats": ("analysis", "teal",
        "a simple geometric castle keep on a small island, encircled by a wide ring of calm water, "
        "seen from a slight angle so both the tower and the full ring of the moat read clearly"),
    "tokenomics": ("analysis", "ember",
        "a circular pie divided into a few clean unequal segments, with a small closed padlock "
        "resting on the largest segment and a short queue of identical round tokens beside it"),
    "red_flags": ("analysis", "crimson",
        "three small triangular pennant flags on thin poles rising out of a descending stepped "
        "line, the middle flag noticeably taller than the others"),

    # ---- STRATEGIES ---------------------------------------------------------
    # NOT a balance scale — risk_reward rejected one and balance_sheet already uses a beam;
    # a third would make the set look like it only owns one idea. Value vs price reads just
    # as well as two objects of obviously different worth standing side by side.
    "buffett_way": ("strategies", "amber",
        "one large multi-faceted cut gemstone standing on a simple flat plinth, and beside it a "
        "short stack of round coins that is plainly much smaller and shorter than the gemstone, "
        "so the gem is clearly worth far more than the little pile being paid for it. Two objects "
        "only, both solidly filled, standing on the same ground line, complete and well inside "
        "the frame"),
    # NOT a shopping cart or basket — etfs_101 already owns the basket. Everyday objects you
    # personally recognise carry "invest in what you know" without reusing that container.
    "lynch_way": ("strategies", "amber",
        "three ordinary everyday objects standing side by side in a row on one shelf — a takeaway "
        "coffee cup with a lid, a running shoe seen from the side, and a smartphone standing "
        "upright — with one clean upward arrow rising behind them. Solid flat fills, all three "
        "objects complete and well inside the frame"),
    # A rising ribbon that shattered into particles was a MOOD, not a statement — no object in
    # it, so nothing for a learner to recognise, and it was the one image in the set that got
    # "I don't understand this". Every other Strategies hero is a thing you know on sight
    # (gemstone, whale, metronome, shears); the rocket keeps that. Chosen over a telescope and
    # a linear-vs-exponential pair — see VARIANTS.
    "cathie_wood_way": ("strategies", "amber",
        "one simple rocket climbing steeply from lower left toward upper right, tilted along its "
        "direction of travel, with a solid tapering exhaust trail curving behind it and three or "
        "four small geometric particles scattered just ahead of its nose. The whole rocket "
        "including its fins and the entire trail are complete and well inside the frame with "
        "clear white margin on every side"),
    "whale_watching": ("strategies", "amber",
        "one large simple solid silhouette of a whale seen from the side gliding below a straight "
        "horizontal surface line, with a small simple boat outline floating on that line above it "
        "and a trail of round bubbles rising between the two. The whole whale including its tail "
        "and the whole boat are complete and well inside the frame"),
    # Green, not the level amber. Same content-driven override as crypto→ember and
    # warnings→crimson: a lesson about tending living things reads wrong in gold.
    # Measured the tightest composition of all 27 on its first render — 76px bottom margin and
    # a lopsided 160/96 left-right split, because the shears drifted out past the third pot.
    # Nothing was technically clipped and the edge guard passed it, which is the point: a
    # composition can read as "cut off" well before anything actually touches an edge. Hence
    # the explicit middle-two-thirds framing and the shears held INSIDE the group.
    "portfolio_gardening": ("strategies", "green",
        "three small potted plants standing in a neat evenly spaced row at clearly different "
        "stages — the first just a sprout with two leaves, the second full and healthy, the third "
        "taller — with a pair of simple open shears held above the third plant, angled in toward "
        "it and kept INSIDE the group rather than out to one side. The three pots and the shears "
        "together occupy only the middle two thirds of the frame width and the middle two thirds "
        "of its height, so there is clearly empty white space on the left, on the right, above "
        "and below. Solid flat fills, all three pots resting on one common ground line whose ends "
        "stop well short of both side edges"),
    # NOT a staircase of blocks — that reads as a bar chart next to the income_statement
    # waterfall, and red_flags already owns steps-with-flags. A metronome is a single object
    # that means steady repetition and appears nowhere else in the 27.
    "power_of_discipline": ("strategies", "amber",
        "one simple upright metronome shaped as a tall narrow trapezoid, with a straight pendulum "
        "arm rising from its base and a small sliding weight partway up the arm, the arm tilted "
        "slightly to one side, and two or three short even arcs behind the arm showing it "
        "swinging steadily. One object only, solidly filled, complete and well inside the frame"),
    # No flag — red_flags owns that shape. Note the endpoints: a "path running left to right"
    # gets drawn spanning the whole frame and off both edges, exactly like the funnel's
    # incoming "band" did. Giving the route an explicit START DOT and an ARROWHEAD forces it
    # to begin and end somewhere inside the picture. The first attempt also ran the route
    # straight THROUGH the middle pit, so not-touching is now stated per pit.
    "common_mistakes": ("strategies", "crimson",
        "three open pits of the same size sunk into a single straight horizontal ground line, "
        "evenly spaced, with clear white space to the left of the first pit and to the right of "
        "the last. Above them one dashed route line that BEGINS at a small solid round dot to the "
        "left of the first pit and ENDS in a clear arrowhead to the right of the last pit, arcing "
        "up and over each pit in turn and never touching, entering or crossing any pit. The "
        "starting dot, the arrowhead, and both ends of the ground line are all well inside the "
        "frame with clear white margin — the ground line must not reach either side edge"),

    # ---- MASTERY ------------------------------------------------------------
    # The six Mastery subjects are the only ones designed rather than drafted: four of these
    # lessons are abstract mental models with no natural object, which is precisely the case
    # that produced the set's one "I don't understand this image". Each names a concrete
    # thing, and each carries the specific negative constraint that its own failure mode
    # needs (blank dominoes so they can't drift into dice; "absolutely not a clock" on the
    # helm; "snapped, not cut" on the bar).
    "fomo_cycle": ("mastery", "crimson",
        "a single flat mousetrap seen from slightly above: a solid rounded rectangular base plate "
        "with all four corners visible, and a heavy U-shaped snap bar cocked and propped clearly "
        "upright at the back of the plate so it reads as loaded and under tension rather than "
        "lying flat. Sitting alone in the exact centre of the plate is one round disc of cheese "
        "with three simple round holes cut through it, filled in the lighter accent colour so it "
        "plainly reads as the lure. Two components only, both bold and chunky, one closed "
        "self-contained object, centred and left-right balanced, complete and well inside the "
        "frame"),
    "second_order_thinking": ("mastery", "violet",
        "three identical upright rectangular domino tiles standing on one short horizontal ground "
        "bar whose two rounded ends stop well inside the picture, evenly spaced and seen straight "
        "on, completely blank apart from a single thin dividing groove across the middle of each "
        "— no pips, no dots, no markings of any kind. The left tile has already toppled and lies "
        "flat on the bar, the middle tile is caught leaning at a clear angle about to strike the "
        "next, and the right-hand tile still stands perfectly upright and untouched, filled in "
        "the lighter accent colour so it reads as the step nobody has asked about yet. Everything "
        "complete, left-right balanced and well inside the frame"),
    "risk_vs_uncertainty": ("mastery", "violet",
        "two solid dice of exactly the same size, the same rounded-corner cube silhouette and the "
        "same slight three-quarter angle, sitting side by side and level on one short straight "
        "base line whose ends stop well inside the picture. The left die is fully pipped, its "
        "three visible faces carrying neat plain solid round dot pips only. The right die is "
        "unmistakably the SAME die with its pips taken away — identical proportions, corners and "
        "angle — but every visible face is left completely bare, an uninterrupted flat fill with "
        "nothing on it at all, and filled in the lighter accent colour. Both are whole closed "
        "cubes with every edge visible, nothing cropped, no cast shadows and no other objects, "
        "complete and well inside the frame"),
    "art_of_selling": ("mastery", "violet",
        "one flat rectangular segmented bar lying horizontally across the middle of the picture, "
        "divided by clean straight chamfered grooves into six equal raised squares like a bar of "
        "chocolate. The bar has been cleanly snapped in two: the left portion of four squares "
        "stays intact and whole, and the right portion of two squares sits slightly apart with a "
        "small clear gap between them and a matching short zigzag break edge on both facing "
        "sides, so it unmistakably reads as broken off rather than cut or sliced. The two "
        "separated squares are filled in the lighter accent colour, and both pieces rest on the "
        "same level line. Only this one bar and its two snapped-off squares — no wrapper, no "
        "crumbs, no knife. Complete, centred, left-right balanced and well inside the frame"),
    "inversion": ("mastery", "violet",
        "a single square maze with thick solid walls, deliberately coarse and simple with only a "
        "few wide corridors, its outer wall closed on all four sides except one clear gap in the "
        "middle of the right-hand wall, and a small solid filled square goal at the very centre. "
        "One bold solid route line in the lighter accent colour BEGINS at a large solid round dot "
        "sitting inside the centre goal square, works outward through the corridors turning three "
        "or four times, and ENDS in a clear arrowhead aimed at the gap but stopping just short of "
        "it, still entirely inside the maze square. Every wrong corridor is left completely "
        "empty. The route is one confident solid stroke of even weight — not dashed, not dotted, "
        "not thin. The maze is one closed self-contained square, complete and well inside the "
        "frame"),
    "ai_and_beyond": ("mastery", "violet",
        "a single ship's steering helm standing upright and centred, drawn as one thick solid "
        "ring with six broad straight spokes meeting at a solid square microchip hub in the exact "
        "centre, and six short rounded handle knobs spaced evenly around the OUTSIDE of the rim "
        "so the silhouette is plainly a wheel you grip and turn. Four short straight circuit "
        "traces run outward from the chip hub along four of the spokes, each ending in a small "
        "filled round node well before it reaches the rim, and the chip hub is filled in the "
        "lighter accent colour. This is a steering wheel and absolutely NOT a clock, dial or "
        "gauge: no tick marks, no notches or markings anywhere around the rim, no needle, no "
        "pointer, no clock hands. One closed self-contained shape, complete and entirely inside "
        "the frame"),
}

# The two subjects the style bake-off runs on: one abstract growth idea (hardest to
# make legible) and one structural document idea (hardest to keep free of text).
STYLE_TEST_SUBJECTS = ("compound_interest", "balance_sheet")

# Alternative subjects for a lesson whose first render missed the idea. `--variants <slug>`
# renders each candidate side by side for a human to pick. Once one wins, paste it into
# LESSONS above and copy its master to _variants/<slug>.chosen.art.jpg — the next run
# adopts it instead of re-rolling, so the approved picture is the one that ships.
VARIANTS = {
    # A rising ribbon that shatters into particles is a MOOD, not a statement — there is
    # nothing in it to recognise, so there is nothing to understand. All three below name a
    # concrete thing: a machine that climbs, two futures compared, or an instrument for
    # seeing further than everyone else.
    # The rocket WON and now lives in LESSONS above; not repeated here. These two are the
    # record of what was weighed against it: "two futures" taught the idea best but read as a
    # chart, and the telescope was the most distinctive object in the 27 but said foresight
    # rather than growth.
    "cathie_wood_way": [
        "two lines that begin together at one small solid dot on the left: the lower line stays "
        "flat and level all the way to the right and ends in a plain blunt cap, while the upper "
        "line bends steadily and then sharply upward and ends in a clear arrowhead high on the "
        "right, so the widening gap between the ordinary path and the accelerating one is the "
        "whole picture. Both lines are solid filled ribbons, and the starting dot, the blunt cap "
        "and the arrowhead are all well inside the frame",

        "one simple telescope on a tripod stand, angled up and to the right, with a small tight "
        "cluster of four-pointed star shapes in the distance along its line of sight. The whole "
        "telescope, all three legs of the tripod and the entire star cluster are complete and "
        "well inside the frame with clear white margin on every side",
    ],
    # The bar/funnel/arrows/bar construction was legible as shapes but unreadable as an
    # IDEA — it never said what was being taken away or what survived. All three below tell
    # the same story left to right or top to bottom: a big amount, visible subtractions, and
    # one small green remainder. Green marks the survivor in every one of them.
    "income_statement": [
        # The waterfall WON and its refined four-bar form now lives in LESSONS above. This
        # five-bar original is kept only as the record of what it grew out of: five bars
        # falling in equal steps read as a decorative pattern rather than a real company,
        # which is why the shipping version pins four bars at deliberately uneven ratios.
        "a row of five solid vertical bars standing on one common baseline, read left to right: "
        "the first bar is very tall and teal, the next three are progressively shorter and each "
        "clearly sits lower than the one before as if a piece has been taken away, and the fifth "
        "and final bar is short and GREEN, the only green element in the picture. Small downward "
        "steps connect the top of each bar to the next so the eye follows the amount shrinking. "
        "All five bars complete, evenly spaced and well inside the frame",

        # The press — the most memorable and least chart-like. Profit as what you actually
        # get after everything is squeezed out.
        "a large solid teal fruit-press or squeezer viewed from the side with a big round mass "
        "held inside it and a plate pressing down from above, and beneath its small spout a short "
        "wide glass holding a small amount of GREEN liquid — the glass obviously holding far less "
        "than the mass being pressed. Two or three small drops falling between the spout and the "
        "glass. The press, the spout and the whole glass are complete and well inside the frame",

        # The stack — the most literal reading of subtraction, and the fastest to parse.
        "a tall stack of identical solid teal blocks resting on a simple base, with three blocks "
        "clearly lifted out of the stack and floating away up and to the right, each with a short "
        "motion line behind it, leaving one single small GREEN block sitting alone on the base — "
        "the green block is the only green element and is plainly much smaller than the original "
        "stack. Everything complete and well inside the frame",
    ],
    # The two runners-up. The interlocking-links candidate WON and now lives in LESSONS
    # above; it is deliberately not repeated here, or `--variants risk_reward` would render
    # the shipping subject twice. Kept as the record of what was considered and rejected.
    "risk_reward": [
        # Split coin — the most instantly readable, and it takes the screen headline
        # ("Two Sides of a Coin") literally. Rejected on the pick.
        "one large round coin seen face on, split down the middle by a single clean vertical "
        "line into two solidly filled halves — the left half carrying a bold upward arrow and "
        "the right half a bold downward arrow — the two halves together forming one complete "
        "unbroken circle that clearly cannot exist without the other. Solid filled shapes, not "
        "thin outlines",

        # Mirrored peak — the climb above the line equals the fall below it. Elegant but the
        # slowest to read, since the whole point is that two distances are equal.
        "a single continuous solid ribbon running left to right that rises into one tall peak and "
        "drops into one trough of exactly equal depth below the same centre line, the height above "
        "and the depth below plainly mirroring each other, with a small upward arrow at the top of "
        "the peak and a small downward arrow at the bottom of the trough. Solid filled ribbon, not "
        "a thin drawn line",
    ],
}

# =============================================================================
BASE = """{style}

SUBJECT: {subject}

PALETTE: {palette} Use two or three colours at most, plus neutrals — restrained and harmonious, never rainbow.

COMPOSITION: horizontal 16:9 frame. ONE single centred subject occupying roughly the middle 70% of the frame, with clean empty margin on all four sides. Calm, balanced and uncluttered — a single clear idea rendered simply, not a busy scene. Everything is fully inside the frame; nothing is cropped by an edge.

BACKGROUND — ABSOLUTE REQUIREMENT: the background is pure flat uniform white #FFFFFF, edge to edge and right into all four corners. No vignette, no gradient, no tinted or coloured backdrop, no background texture, no ground plane, no horizon line, no shadow reaching any edge, no border, no frame, no rounded card behind the subject.

ABSOLUTE REQUIREMENT: NO text, letters, numbers, digits, words, labels, axis ticks, currency symbols, percentage signs, logos, watermarks or signage ANYWHERE in the image. No human faces, no hands, no people."""


def _load_authored_slugs() -> set[str]:
    data = json.loads(LESSONS_JSON.read_text())
    return {l["slug"] for l in data["lessons"]}


def _check_lesson_table() -> None:
    """A 28th lesson must never ship with a blank hero, and a typo'd key must never
    silently generate art nothing points at."""
    authored = _load_authored_slugs()
    missing, orphan = authored - set(LESSONS), set(LESSONS) - authored
    if missing or orphan:
        raise SystemExit(
            f"LESSONS is out of sync with {LESSONS_JSON.name}: "
            f"missing={sorted(missing)} orphan={sorted(orphan)}")


def prompt_for(slug: str, style: str = None) -> str:
    _level, palette_name, subject = LESSONS[slug]
    return BASE.format(style=STYLES[style or STYLE],
                       subject=subject, palette=PALETTES[palette_name])


def manifest_path(slug: str) -> Path:
    return OUT / f"{slug}.manifest.json"


def read_manifest(slug: str) -> dict:
    p = manifest_path(slug)
    return json.loads(p.read_text()) if p.exists() else {}


def write_manifest(slug: str, data: dict) -> None:
    manifest_path(slug).write_text(json.dumps(data, indent=2) + "\n")


def white_report(path: Path) -> dict:
    """Sample the border ring. The iOS plate is #FFFFFF, so anything dimmer or tinted
    here shows as a visible rectangle seam inside the plate in dark mode."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        px = im.load()
        r = WHITE_RING_PX
        pts = []
        for x in range(0, w, max(1, w // 64)):
            pts.append(px[x, min(r, h - 1)])
            pts.append(px[x, max(0, h - 1 - r)])
        for y in range(0, h, max(1, h // 64)):
            pts.append(px[min(r, w - 1), y])
            pts.append(px[max(0, w - 1 - r), y])
        for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
            pts.append(px[cx, cy])

    flat = [c for p in pts for c in p]
    min_channel = min(flat)
    spread = max(max(p) - min(p) for p in pts)          # per-pixel colour cast
    return {
        "min_channel": min_channel,
        "max_cast": spread,
        "corner_rgb": list(pts[-4]),
        "is_white": min_channel >= WHITE_MIN_CHANNEL and spread <= WHITE_MAX_SPREAD,
        # Two different failures land in the same measurement, and they need different
        # fixes. A near-255 miss is a TINTED background — the snap handles it. A dark or
        # saturated border pixel means the SUBJECT is running off the edge, which is a
        # composition failure the snap cannot touch and a re-roll of the same prompt
        # probably repeats. Naming it tells you which one you have.
        "art_at_edge": min_channel < 200 or spread > 40,
    }


def _snap_white(im):
    """Force every already-visually-white pixel to exactly #FFFFFF so the art dissolves
    into the iOS plate instead of showing a faint rectangle edge in dark mode."""
    import numpy as np

    a = np.asarray(im, dtype=np.uint8)
    mn = a.min(axis=2)
    cast = a.max(axis=2).astype(np.int16) - mn.astype(np.int16)
    mask = (mn >= SNAP_MIN_CHANNEL) & (cast <= SNAP_MAX_CAST)
    if not mask.any():
        return im, 0.0
    a = a.copy()
    a[mask] = 255
    from PIL import Image
    return Image.fromarray(a, "RGB"), round(float(mask.mean()), 4)


def _derive_hero(master: Path, dest: Path) -> dict:
    """Center-crop to exactly 16:9 if needed, snap the white, resize to shipping size."""
    from PIL import Image

    with Image.open(master) as im:
        im = im.convert("RGB")
        w, h = im.size
        target = HERO_W / HERO_H
        cropped = False
        if abs(w / h - target) > 0.005:
            cropped = True
            if w / h > target:                          # too wide — trim sides
                nw = int(round(h * target))
                left = (w - nw) // 2
                im = im.crop((left, 0, left + nw, h))
            else:                                       # too tall — trim top/bottom
                nh = int(round(w / target))
                top = (h - nh) // 2
                im = im.crop((0, top, w, top + nh))
        # Snap BEFORE the resize: afterwards it would harden the antialiased edges
        # where the art meets the background into jaggies.
        im, snapped = _snap_white(im)
        im = im.resize((HERO_W, HERO_H), Image.LANCZOS)
        tmp = dest.with_suffix(".part")
        im.save(tmp, "JPEG", quality=HERO_QUALITY, optimize=True, progressive=True)
        os.replace(tmp, dest)

    data = dest.read_bytes()
    return {"file": dest.name, "width": HERO_W, "height": HERO_H,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "cropped": cropped, "snapped_fraction": snapped}


def _call_model(prompt: str, label: str) -> bytes:
    """One image, with the book-cover retry semantics: retry transient empties and
    transport errors, fail fast on a genuine refusal (retrying one only burns quota)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=KEY)
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.models.generate_content(
                model=MODEL, contents=prompt,
                # NOTE: person_generation is Vertex-only; the Gemini API rejects it.
                # People are excluded by the prompt, not by a parameter.
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="16:9", image_size="2K"),
                ),
            )
        except ValueError as e:
            raise RuntimeError(f"{label}: bad request config: {e}") from e
        except Exception as e:  # noqa: BLE001
            last = e
            wait = min(8 * attempt, 60)
            print(f"     ! {type(e).__name__}: {str(e)[:90]} — retry {wait}s")
            time.sleep(wait)
            continue

        cands = resp.candidates or []
        if not cands:
            raise RuntimeError(f"{label}: no candidates. "
                               f"prompt_feedback={getattr(resp, 'prompt_feedback', None)}")
        cand = cands[0]
        parts = (cand.content.parts if cand.content else None) or []
        # Walk for inline_data — never index parts[0]; a refusal comes back as a text part.
        blob = next((p.inline_data.data for p in parts
                     if getattr(p, "inline_data", None) and p.inline_data.data), None)
        if blob is not None:
            return blob

        txt = " ".join((p.text or "") for p in parts if getattr(p, "text", None))
        reason = str(cand.finish_reason)
        transient = ("NO_IMAGE" in reason and not cand.safety_ratings and not txt.strip())
        if transient and attempt < MAX_ATTEMPTS:
            wait = min(6 * attempt, 30)
            print(f"     ! empty result ({reason}) — retry {wait}s")
            time.sleep(wait)
            continue
        raise RuntimeError(f"{label}: model returned no image. finish_reason={reason} "
                           f"safety={cand.safety_ratings} text={txt[:300]!r}")

    raise RuntimeError(f"{label}: exhausted {MAX_ATTEMPTS} attempts: "
                       f"{type(last).__name__}: {last}")


def _write_master(blob: bytes, dest: Path) -> tuple[int, int]:
    from PIL import Image

    img = Image.open(io.BytesIO(blob))
    img.load()
    tmp = dest.with_suffix(".part")
    img.convert("RGB").save(tmp, "JPEG", quality=95, subsampling=0, optimize=True)
    os.replace(tmp, dest)                       # atomic: a Ctrl-C cannot leave a half file
    with Image.open(dest) as c:
        c.verify()                              # headers
    with Image.open(dest) as c:
        c.load()                                # and the body — verify() passes on a truncation
        return c.size


def generate(slug: str, force: bool = False) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    level, palette, subject = LESSONS[slug]
    art = OUT / f"{slug}.art.jpg"
    hero = OUT / f"{slug}.hero.jpg"
    prompt = prompt_for(slug)
    sha = hashlib.sha1(prompt.encode()).hexdigest()[:12]
    man = read_manifest(slug)

    if art.exists() and hero.exists() and man.get("art_prompt_sha1") == sha and not force:
        print(f"  {slug:24s} [up to date — no API call]")
        return hero

    # Archive the outgoing master BEFORE overwriting it. A re-roll is destructive and the
    # previous picture is often still wanted — "keep the old X but the new Y" is a normal
    # note, and it is unanswerable once the file is gone. Keyed by the prompt hash that
    # produced it, so each archived version is traceable to its own subject text.
    if art.exists():
        prev_dir = OUT / "_prev"
        prev_dir.mkdir(parents=True, exist_ok=True)
        old_sha = man.get("art_prompt_sha1", "unknown")
        shutil.copyfile(art, prev_dir / f"{slug}.{old_sha}.art.jpg")

    blob = _call_model(prompt, slug)
    w, h = _write_master(blob, art)
    rec = _derive_hero(art, hero)
    white = white_report(hero)          # measure what actually ships, post-snap

    man.update(slug=slug, level=level, style=STYLE, palette=palette, subject=subject,
               art_model=MODEL, art_aspect="16:9", art_size="2K",
               art_prompt=prompt, art_prompt_sha1=sha,
               art_file=art.name, art_native_width=w, art_native_height=h,
               masters={"hero": rec}, background=white)
    write_manifest(slug, man)

    if white["is_white"]:
        flag = ""
    elif white["art_at_edge"]:
        flag = f"  ⚠ SUBJECT RUNS OFF THE EDGE (border min={white['min_channel']})"
    else:
        flag = f"  ⚠ tinted background (min={white['min_channel']} cast={white['max_cast']})"
    print(f"  {slug:24s} + {w}x{h} → hero {rec['bytes'] // 1024} KB{flag}")
    return hero


def _adoptable_master(slug: str) -> Path | None:
    """An already-approved master for this lesson, if one exists. A hand-picked variant
    outranks a bake-off sample — it is the later and more specific decision."""
    for p in (VAR_OUT / f"{slug}.chosen.art.jpg", STYLE_OUT / f"{STYLE}_{slug}.art.jpg"):
        if p.exists():
            return p
    return None


def adopt_style_sample(slug: str) -> bool:
    """Promote an approved sample or picked variant to be the lesson's shipping art.

    Re-generating would produce a DIFFERENT picture from the identical prompt — which is
    exactly what signing one off is meant to prevent. Adoption is free and keeps the
    approved image. The prompt hash is written unchanged, so a later full run treats the
    lesson as up to date and makes no API call.
    """
    src = _adoptable_master(slug)
    if src is None:
        return False

    OUT.mkdir(parents=True, exist_ok=True)
    art, hero = OUT / f"{slug}.art.jpg", OUT / f"{slug}.hero.jpg"
    prompt = prompt_for(slug)
    sha = hashlib.sha1(prompt.encode()).hexdigest()[:12]
    man = read_manifest(slug)
    if art.exists() and hero.exists() and man.get("art_prompt_sha1") == sha:
        return False                                    # already adopted

    from PIL import Image
    shutil.copyfile(src, art)
    with Image.open(art) as c:
        c.load()
        w, h = c.size
    rec = _derive_hero(art, hero)
    white = white_report(hero)
    level, palette, subject = LESSONS[slug]
    man.update(slug=slug, level=level, style=STYLE, palette=palette, subject=subject,
               art_model=MODEL, art_aspect="16:9", art_size="2K",
               art_prompt=prompt, art_prompt_sha1=sha,
               art_file=art.name, art_native_width=w, art_native_height=h,
               masters={"hero": rec}, background=white, adopted_from=src.name)
    write_manifest(slug, man)
    print(f"  {slug:24s} = adopted approved sample ({src.name})")
    return True


def recheck(slugs: list[str]) -> int:
    """Recompute the background report for art already on disk. FREE — no API call.

    Manifests written before a new check existed are missing its fields, so a parity test
    reading them would KeyError on exactly the images that predate the guard. This brings
    every manifest up to the current shape without touching a pixel.
    """
    changed, bad = 0, []
    for slug in slugs:
        hero = OUT / f"{slug}.hero.jpg"
        man = read_manifest(slug)
        if not hero.exists() or not man:
            continue
        white = white_report(hero)
        if man.get("background") != white:
            man["background"] = white
            write_manifest(slug, man)
            changed += 1
        if not white["is_white"]:
            bad.append(slug)
    print(f"rechecked {len(slugs)} lesson(s) · {changed} manifest(s) updated · no API calls")
    if bad:
        print("⚠ still failing the background check:", bad)
    return len(bad)


def run_variants(slug: str, force: bool = False) -> None:
    """Render every alternative subject for one lesson so a human can pick between them."""
    if slug not in VARIANTS:
        raise SystemExit(f"no VARIANTS entry for {slug!r} — add candidate subjects first")

    VAR_OUT.mkdir(parents=True, exist_ok=True)
    _level, palette_name, current = LESSONS[slug]
    # Tag carries a hash of the SUBJECT, not just its position. Positional names silently
    # re-associate an existing image with a different prompt the moment the list is edited
    # — you would be comparing last round's picture against this round's caption.
    def _tag(i, subject):
        return f"{i}_{hashlib.sha1(subject.encode()).hexdigest()[:6]}"

    cands = [("0_current", current)] + [(_tag(i, s), s)
                                        for i, s in enumerate(VARIANTS[slug], 1)]
    print(f"Variants for {slug} — {len(cands)} candidate(s) · {MODEL} · 16:9 · 2K "
          f"· style={STYLE}\n")

    index, failed = [], []
    for i, (tag, subject) in enumerate(cands):
        label = f"{slug}#{tag}"
        master = VAR_OUT / f"{slug}_{tag}.art.jpg"
        hero = VAR_OUT / f"{slug}_{tag}.jpg"
        prompt = BASE.format(style=STYLES[STYLE], subject=subject,
                             palette=PALETTES[palette_name])
        try:
            # The current subject already has art — never pay to reproduce it.
            live = OUT / f"{slug}.art.jpg"
            if tag == "0_current" and live.exists() and not force:
                shutil.copyfile(live, master)
                from PIL import Image
                with Image.open(master) as c:
                    c.load()
                    w, h = c.size
            elif master.exists() and not force:
                from PIL import Image
                with Image.open(master) as c:
                    c.load()
                    w, h = c.size
            else:
                blob = _call_model(prompt, label)
                w, h = _write_master(blob, master)
                if i < len(cands) - 1:
                    time.sleep(THROTTLE)
            rec = _derive_hero(master, hero)
            white = white_report(hero)
            index.append({"slug": slug, "tag": tag, "subject": subject, "file": hero.name,
                          "master": master.name, "prompt": prompt, "background": white,
                          "native": [w, h], "bytes": rec["bytes"]})
            flag = "" if white["is_white"] else f"  ⚠ BG min={white['min_channel']}"
            print(f"  {label:26s} + {w}x{h} → {rec['bytes'] // 1024} KB{flag}")
        except Exception as e:  # noqa: BLE001
            print(f"  {label:26s} FAILED {type(e).__name__}: {str(e)[:200]}")
            failed.append(label)

    (VAR_OUT / f"{slug}.index.json").write_text(json.dumps(
        {"slug": slug, "style": STYLE, "palette": palette_name,
         "candidates": index}, indent=2) + "\n")
    print(f"\n{len(cands) - len(failed)}/{len(cands)} candidate(s) in {VAR_OUT}")
    print(f"To adopt one: paste its subject into LESSONS[{slug!r}], then\n"
          f"  cp {VAR_OUT}/{slug}_<tag>.art.jpg {VAR_OUT}/{slug}.chosen.art.jpg\n"
          f"  ./venv/bin/python scripts/generate_journey_art.py --only {slug}")
    if failed:
        sys.exit(1)


def run_style_bakeoff(styles: list[str], force: bool = False) -> None:
    """Render every candidate style against the two fixed test subjects so a human can
    pick one. Writes _style/index.json for the comparison page."""
    STYLE_OUT.mkdir(parents=True, exist_ok=True)
    index = []
    jobs = [(s, subj) for s in styles for subj in STYLE_TEST_SUBJECTS]
    print(f"Style bake-off — {len(styles)} style(s) x {len(STYLE_TEST_SUBJECTS)} subject(s) "
          f"= {len(jobs)} image(s) · {MODEL} · 16:9 · 2K\n")

    failed = []
    for i, (style, subj) in enumerate(jobs):
        label = f"{style}/{subj}"
        try:
            prompt = prompt_for(subj, style=style)
            master = STYLE_OUT / f"{style}_{subj}.art.jpg"
            hero = STYLE_OUT / f"{style}_{subj}.jpg"
            # Reuse an existing master so re-deriving (a crop/snap/quality change) is
            # free — only a missing master or --force costs an API call.
            if master.exists() and not force:
                from PIL import Image
                with Image.open(master) as c:
                    w, h = c.size
            else:
                blob = _call_model(prompt, label)
                w, h = _write_master(blob, master)
            rec = _derive_hero(master, hero)
            white = white_report(hero)
            index.append({"style": style, "subject": subj, "file": hero.name,
                          "palette": LESSONS[subj][1], "level": LESSONS[subj][0],
                          "prompt": prompt, "background": white,
                          "native": [w, h], "bytes": rec["bytes"]})
            flag = "" if white["is_white"] else f"  ⚠ BG min={white['min_channel']}"
            print(f"  {label:34s} + {w}x{h} → {rec['bytes'] // 1024} KB{flag}")
        except Exception as e:  # noqa: BLE001
            print(f"  {label:34s} FAILED {type(e).__name__}: {str(e)[:200]}")
            failed.append(label)
        if i < len(jobs) - 1:
            time.sleep(THROTTLE)

    (STYLE_OUT / "index.json").write_text(json.dumps(
        {"model": MODEL, "styles": {k: STYLES[k] for k in styles},
         "test_subjects": list(STYLE_TEST_SUBJECTS), "samples": index}, indent=2) + "\n")
    print(f"\n{len(jobs) - len(failed)}/{len(jobs)} sample(s) in {STYLE_OUT}")
    if failed:
        print("failed:", failed)
        sys.exit(1)


def main():
    _check_lesson_table()
    argv = sys.argv[1:]
    force = "--force" in argv

    if "--style" in argv:
        want = [a for a in argv if a in STYLES]
        run_style_bakeoff(want or list(STYLES), force)
        return

    if "--variants" in argv:
        i = argv.index("--variants")
        if i + 1 >= len(argv):
            raise SystemExit("--variants needs a lesson slug")
        run_variants(argv[i + 1], force)
        return

    only = [argv[i + 1] for i, a in enumerate(argv) if a == "--only" and i + 1 < len(argv)]
    unknown = [s for s in only if s not in LESSONS]
    if unknown:
        raise SystemExit(f"unknown lesson slug(s): {unknown}")

    levels = [argv[i + 1] for i, a in enumerate(argv) if a == "--level" and i + 1 < len(argv)]
    bad = [lv for lv in levels if lv not in LEVEL_PALETTE]
    if bad:
        raise SystemExit(f"unknown level(s): {bad} — expected {sorted(LEVEL_PALETTE)}")

    slugs = only or [s for s, (lv, _p, _sub) in LESSONS.items()
                     if not levels or lv in levels]

    if "--recheck" in argv:
        sys.exit(1 if recheck(slugs) else 0)

    print(f"Journey art — {len(slugs)} lesson(s) · {MODEL} · 16:9 · 2K · style={STYLE}\n")
    failed, off_white = [], []
    for i, slug in enumerate(slugs):
        try:
            if force or not adopt_style_sample(slug):
                generate(slug, force)
            if not read_manifest(slug).get("background", {}).get("is_white", True):
                off_white.append(slug)
        except Exception as e:  # noqa: BLE001
            print(f"  {slug:24s} FAILED {type(e).__name__}: {str(e)[:300]}")
            failed.append(slug)
        if i < len(slugs) - 1:
            time.sleep(THROTTLE)

    print(f"\n{len(slugs) - len(failed)}/{len(slugs)} hero(es) in {OUT}")
    if off_white:
        edged = [s for s in off_white
                 if read_manifest(s).get("background", {}).get("art_at_edge")]
        tinted = [s for s in off_white if s not in edged]
        if edged:
            print("⚠ subject runs off the frame edge — REWRITE the subject, do not just "
                  "re-roll the same prompt:", edged)
        if tinted:
            print("⚠ tinted background (re-roll with --force):", tinted)
    if failed:
        print("failed:", failed)
        sys.exit(1)
    print("Next: ./venv/bin/python scripts/seed_journey.py --dry-run")


if __name__ == "__main__":
    main()
