# Whale girl expressions v2

Created 2026-10-27 using the built-in image_gen tool. No fallback CLI or API key was used. `whale-girl-sheet-v1.png` is the character reference; its three existing poses remain unchanged.

- Source: `whale-girl-expressions-v2.png`, 1881×836 RGBA, real alpha channel.
- SHA-256: `3d9ba5a8cae5b5c61b53dbdb4ba83c34b1c9b1db8674453c3cd72ce9a6f00cb5`.
- Left to right: pose 4 sleepy / hugging an empty bowl; pose 5 happy anticipation / steaming rice; pose 6 sheepish / burnt rice and a sweat bead.
- Source X regions: `[0,654)`, `[654,1290)`, `[1290,1881)`. The first opaque-content gap is X 652–655, the second is X 1281–1293. Crop each region to its opaque bounding box before quantizing.
- The artwork is a source asset, not the device framebuffer. `npm run assets` converts it into palette-encoded rectangle strings. The generated `character-build.json` records actual geometry counts. Runtime art uses the existing eight-color palette and 32×40 virtual coordinates.
- The three new API 5 sprites have 187 / 192 / 198 rectangles at 32×40. API 4 sprites have 90 / 95 / 98 rectangles at 18×26. Existing poses are unchanged; no frame exceeds 232 / 100 rectangles.
- Software-rendered preview of the encoded primitives is generated at `.cache/character-preview.png` (six poses, two rows). This preview does not prove physical display behavior.

## Full generation prompt

```text
Use case: stylized-concept.
Asset type: supplementary transparent pixel-art expression sprite sheet for a 240x240 embedded virtual pet game.
Input image 1 is a CHARACTER REFERENCE ONLY. Preserve the exact recognizable identity and clothing of this same blue-haired chibi whale girl: enormous cute head, long deep-blue hair, blue whale fin ear accents and whale tail, white frilled headband, dark navy and white modest maid-style dress and white apron, white socks and dark shoes. Keep her friendly and all-ages. Match the reference's full-body proportions and front view.
Make ONE wide sprite sheet with exactly THREE evenly spaced full-body versions in a horizontal row, full bodies on a common baseline, separated by generous transparent gutters; no overlap.
LEFT: sleepy and lazy, eyelids drooped almost closed, tiny relaxed mouth, arms hugging an empty white rice bowl to her tummy, head very slightly tilted. The sleepy expression must be clearly readable.
CENTER: greedily sniffing a delicious bowl of white steamed rice, bowl held directly below nose, eyes blissfully closed, delighted blush and small open smiling mouth, two tiny simple steam curls over rice; she is leaning forward only slightly.
RIGHT: awkward guilty side-eye after burning the rice, pupils visibly looking sideways, small sheepish closed mouth, one large comic sweat bead near temple, hands hiding a small charcoal-dark rice bowl at tummy level. Keep it gentle and funny.
EXTREMELY SIMPLE LOW-RES PIXEL ART: each character should look designed on exactly a 32 by 40 logical pixel grid, enlarged with nearest-neighbor pixels. Use 8 FLAT SOLID colors for the entire sheet, dark blue outlines, broad connected color blocks, large simple features. Simplify hair into broad blue shapes and dress ruffles into few blocks. It must stay readable when sampled to 32x40. Avoid dense decoration, many small individual pixels, tiny strands, texture and delicate shading.
True transparent alpha background. No floor, shadow, scenery, gradient, glow or antialiasing. No text, cell labels, grid, borders, logo, watermark, extra people or creatures. The poses vary but identity, proportions and dress remain consistent.
```
