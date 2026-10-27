# Whale girl sprite sheet v1

Created 2026-10-27 with the built-in image_gen tool, using the user-provided character reference. No API key or fallback CLI was used.

- File: `whale-girl-sheet-v1.png`
- Content: three poses of the same blue-haired whale girl, from left to right: idle, rice bowl / eating, discovery with shell.
- Reference: the character image supplied by the user in this chat. The project does not claim official DeepSeek affiliation or ownership of the reference design.
- Background: generated with `transparent_background: true`; preserve alpha during compilation.
- Runtime: this is a source artwork asset. The device does not directly support PNG in the currently inspected drawing API. The application asset compiler must sample and palette-encode it for device rendering; the image itself is not proof of the runtime shape budget.
- Generation result is more detailed than the requested logical grid. Validate the final encoded sprite and shape count after downsampling, rather than assuming a 32×40 grid or <=220 rectangles.
- Image metadata: 1881×836, RGBA, alpha range 0–255. The generated cells are not mathematically equal-width; do not cut at exact thirds, which would clip the tails.
- Suggested source cell X ranges: idle `[0, 654)`, eating `[654, 1287)`, discovery `[1287, 1881)`, using the full Y range before finding each sprite's opaque bounding box. Opaque-content gaps (alpha >200) were observed at X 642–666 and X 1268–1306. Preserve a consistent character scale/baseline when encoding.

## Full generation prompt

```text
Use case: stylized-concept.
Asset type: transparent pixel-art sprite sheet for a 240x240 embedded virtual-pet game.
Input image 1 is a CHARACTER REFERENCE ONLY. Preserve its recognizable chibi whale-girl design: very large cute head, long deep-blue hair, blue whale fin ear accents, a whale tail, white frilled headband, dark navy and white modest maid-style dress with apron, white socks and dark shoes. Keep her friendly and all-ages.
Create ONE wide transparent PNG sprite sheet containing exactly THREE equal-width cells in one horizontal row. The same character has identical proportions and clothing in every cell. Each full-body sprite stands on the same baseline and fits completely within its own cell, with generous transparent margins and no overlapping cells.
Left cell: idle, gentle smile, hands relaxed.
Middle cell: holding a white bowl of steamed white rice with both hands at chest level, eyes happily closed and cheeks round as she eats.
Right cell: delighted discovery, bright happy eyes and one hand raised, holding a tiny simple pale-blue seashell in the other hand.
EXTREMELY SIMPLE LOW-RES PIXEL ART. Each character should be designed as if on an exact 32 by 40 logical pixel grid enlarged with nearest-neighbor scaling. Use only 8 to 10 FLAT SOLID colors across the whole sheet. Large crisp square pixels and large contiguous color regions, a simple dark outline, simple one-pixel eyes and mouth; preserve the blue hair, white apron and whale silhouette. Simplify dress frills and hair into a few broad blocks. The final sprites will be encoded as rectangles, so minimize disconnected tiny pixel details.
Transparent background with a real alpha channel. No ground, no shadows, no scenery, no gradients, no glow, no anti-aliasing, no smooth painterly details. No text, cell labels, grid lines, borders, logos or watermarks. Exactly three versions of one character, no extra people or creatures.
```
