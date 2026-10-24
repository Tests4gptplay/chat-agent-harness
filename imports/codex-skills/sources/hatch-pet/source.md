# V2 Animation Rows

Every newly hatched pet uses an 8-column x 11-row atlas with 192x208 cells. The final atlas is 1536x2288 and uses `spriteVersionNumber: 2`.

| Row | State             | Used columns | Durations                                              |
| --- | ----------------- | -----------: | ------------------------------------------------------ |
| 0   | idle              |          0-5 | 280, 110, 110, 140, 140, 320 ms                        |
| 1   | running-right     |          0-7 | 120 ms each, final 220 ms                              |
| 2   | running-left      |          0-7 | 120 ms each, final 220 ms                              |
| 3   | waving            |          0-3 | 140 ms each, final 280 ms                              |
| 4   | jumping           |          0-4 | 140 ms each, final 280 ms                              |
| 5   | failed            |          0-7 | 140 ms each, final 240 ms                              |
| 6   | waiting           |          0-5 | 150 ms each, final 260 ms                              |
| 7   | running           |          0-5 | 120 ms each, final 220 ms                              |
| 8   | review            |          0-5 | 150 ms each, final 280 ms                              |
| 9   | look directions A |          0-7 | 000, 022.5, 045, 067.5, 090, 112.5, 135, 157.5 degrees |
| 10  | look directions B |          0-7 | 180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5 degrees |

Unused cells after each standard animation row's final used column must be fully transparent. All look-row cells are used.

`000` degrees means looking up / 12 o'clock. Neutral/front is the pointer deadzone and falls back to the normal idle animation.

## Row Purposes

- `idle`: calm, low-distraction breathing/blinking loop and reduced-motion first frame.
- `running-right`: locomotion to the right with a readable alternating cadence.
- `running-left`: locomotion to the left; mirror only when identity and prop handedness remain correct, preserving frame order.
- `waving`: greeting or attention gesture with a clear start, raised gesture, and return.
- `jumping`: anticipation, lift, peak, descent, and settle.
- `failed`: readable error, sad, or deflated reaction without noisy detached effects.
- `waiting`: expectant asking pose for approval, help, or user input.
- `running`: active task work or processing, not literal foot-running.
- `review`: focused inspection of completed output.
- rows `9-10`: one continuous clockwise 16-pose look loop using pet-specific eye, head, body, appendage, and prop mechanics.




# Codex V2 Pet Contract

## Sprite Atlas

- Version: `spriteVersionNumber: 2`.
- Format: PNG or WebP.
- Dimensions: `1536x2288`.
- Grid: 8 columns x 11 rows.
- Cell: `192x208`.
- Background: transparent.
- Rows `0-8`: standard animation states.
- Rows `9-10`: 16 clockwise look directions.
- Unused standard-row cells: fully transparent.

The 8x9 `1536x1872` atlas is an intermediate assembly artifact only. Never package it as a newly hatched pet.

## Look Directions

- Row `9`: `000`, `022.5`, `045`, `067.5`, `090`, `112.5`, `135`, `157.5` degrees.
- Row `10`: `180`, `202.5`, `225`, `247.5`, `270`, `292.5`, `315`, `337.5` degrees.
- `000` means up / 12 o'clock, not neutral/front.
- Neutral/front is the no-vector deadzone and falls back to idle.

## Local Custom Pet Package

Place files under:

```text
${CODEX_HOME:-$HOME/.codex}/pets/<pet-name>/
├── pet.json
└── spritesheet.webp
```

Required manifest shape:

```json
{
  "id": "pet-name",
  "displayName": "Pet Name",
  "description": "One short sentence.",
  "spriteVersionNumber": 2,
  "spritesheetPath": "spritesheet.webp"
}
```

The app derives the 11-row layout and look-direction behavior from `spriteVersionNumber: 2`. Omitting it defaults the pet to v1 and causes the 2288-pixel-tall spritesheet to be rejected.




# V2 Pet QA Rubric

Do not package a pet until every section passes.

## Geometry And Package

- Final atlas is exactly `1536x2288`, 8 columns x 11 rows, with `192x208` cells.
- `pet.json` contains `spriteVersionNumber: 2` and points to the packaged spritesheet.
- Used cells are non-empty; unused standard-row cells are transparent.
- Fully transparent pixels have zero RGB residue.
- The 8x9 intermediate atlas is never packaged.
- `qa/review.json` has no errors.
- Standard rows use component extraction unless `stable-slots` was deliberately approved after playback review.
- Coherent look rows recover their ordered pose groups and pass near-edge clipping checks after shared-scale registration into final cells.

## Character And Style

- Silhouette, proportions, face, expression language, material, palette, lighting, markings, and props remain the same across all 11 rows.
- The pet reads clearly inside a `192x208` cell in the chosen style.
- No frame introduces an unintended character, object, logo, text, scene, or effect.

## Standard Animation

- Rows `0-8` contain the exact required frame counts and recognizable state semantics.
- Loops do not pop, reverse cadence, face the wrong direction, or remain effectively static.
- The first idle frame works as a reduced-motion still.
- `waiting`, `running`, `review`, and `failed` remain visually distinct.

## Look Directions

- All 16 directions are present in fixed clockwise order and visibly distinct from neutral/rest.
- Cardinal directions read unmistakably as up, right, down, and left; diagonals and intermediates read in the correct quadrant.
- `qa/look-directions.png` includes full-body and zoomed head/upper-body views.
- `qa/direction-semantics.json` records `pass`, `expected`, `observed`, and `reason` for every direction.
- `qa/look-continuity.json` has no unexplained holes, center jumps, area jumps, or local difference outliers.
- Eyes, eyelids, head, body, appendages, and props follow the pet-specific look mechanics plan.
- No whole-sprite rotation, replacement/googly eyes, visual clipping, seam bands, or transparent interior holes.
- A repaired direction is approved by an independent visual QA worker or explicit user inspection, not the repairing parent alone.

## Repair Policy

Repair the smallest packaging-eligible scope: one standard row or one complete coherent look row. Never mix an individually generated repair cell into a new pet's final look row. Re-run assembly, deterministic validation, direction QA, continuity measurement, and semantic review after every relevant repair.




