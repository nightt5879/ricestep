// Lossless layered rectangle encoding for tiny indexed sprites.
// Pixels are -1 (scene background) or 0..paletteCount-1. Rectangles may overlap.
// Only later colours may be overpainted, so repainting the layers is exact.
'use strict';

function encode(pixels, width, height, paletteCount) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || pixels.length !== width * height) {
    throw new Error('Invalid indexed image dimensions');
  }
  if (!Number.isInteger(paletteCount) || paletteCount < 1 || paletteCount > 8) {
    throw new Error('paletteCount must be an integer from 1 to 8');
  }
  const targets = Array.from({length: paletteCount + 1}, () => []);
  let fullMask = 0;
  for (let i = 0; i < pixels.length; i++) {
    const c = pixels[i];
    if (!Number.isInteger(c) || c < -1 || c >= paletteCount) throw new Error('Invalid palette index');
    targets[c + 1].push([i % width, Math.floor(i / width)]);
    fullMask |= 1 << (c + 1);
  }

  const candidatesByMask = new Map();
  function candidates(mask) {
    if (candidatesByMask.has(mask)) return candidatesByMask.get(mask);
    const rectangles = [];
    const forbidden = new Uint8Array(pixels.length);
    for (let i = 0; i < pixels.length; i++) forbidden[i] = (mask & (1 << (pixels[i] + 1))) !== 0 ? 1 : 0;
    for (let top = 0; top < height; top++) {
      const allowed = new Uint8Array(width).fill(1);
      for (let bottom = top; bottom < height; bottom++) {
        for (let x = 0; x < width; x++) if (forbidden[bottom * width + x]) allowed[x] = 0;
        for (let x = 0; x < width;) {
          if (!allowed[x]) { x++; continue; }
          const left = x;
          while (x < width && allowed[x]) x++;
          const right = x;
          let extendsUp = top > 0, extendsDown = bottom + 1 < height;
          for (let xx = left; xx < right && (extendsUp || extendsDown); xx++) {
            if (extendsUp && forbidden[(top - 1) * width + xx]) extendsUp = false;
            if (extendsDown && forbidden[(bottom + 1) * width + xx]) extendsDown = false;
          }
          if (!extendsUp && !extendsDown) rectangles.push([left, top, right - left, bottom - top + 1]);
        }
      }
    }
    candidatesByMask.set(mask, rectangles);
    return rectangles;
  }

  const layers = new Map();
  function layer(mask, index) {
    const key = mask * 9 + index;
    if (layers.has(key)) return layers.get(key);
    // The frame buffer already starts in the transparent / scene colour.
    if (index === 0 && mask === 0) { layers.set(key, []); return []; }
    const points = targets[index];
    const choices = [];
    const links = points.map(() => []);
    for (const rect of candidates(mask)) {
      const [x, y, w, h] = rect;
      const covered = [];
      for (let p = 0; p < points.length; p++) {
        const [px, py] = points[p];
        if (px >= x && px < x + w && py >= y && py < y + h) covered.push(p);
      }
      if (covered.length) {
        const id = choices.length;
        choices.push({rect, covered, score: covered.length});
        for (const p of covered) links[p].push(id);
      }
    }
    const active = new Uint8Array(points.length).fill(1);
    let remaining = points.length;
    const output = [];
    while (remaining > 0) {
      let best = -1, bestScore = 0, bestArea = Infinity;
      for (let i = 0; i < choices.length; i++) {
        const choice = choices[i];
        const area = choice.rect[2] * choice.rect[3];
        if (choice.score > bestScore || (choice.score === bestScore && area < bestArea)) {
          best = i; bestScore = choice.score; bestArea = area;
        }
      }
      if (best < 0 || bestScore === 0) throw new Error('Internal rectangle-cover failure');
      let left = width, top = height, right = -1, bottom = -1;
      // Trim surplus wildcard pixels; this can save background restoration work.
      for (const p of choices[best].covered) if (active[p]) {
        const [px, py] = points[p];
        left = Math.min(left, px); top = Math.min(top, py);
        right = Math.max(right, px); bottom = Math.max(bottom, py);
        active[p] = 0; remaining--;
        for (const id of links[p]) choices[id].score--;
      }
      output.push([left, top, right - left + 1, bottom - top + 1, index - 1]);
    }
    layers.set(key, output);
    return output;
  }

  // Exact dynamic programming over the colour order, using a deterministic
  // greedy cover inside each layer. Every candidate sequence remains lossless.
  const count = new Float64Array(1 << (paletteCount + 1)).fill(Infinity);
  const area = new Float64Array(count.length).fill(Infinity);
  const previous = new Int16Array(count.length).fill(-1);
  const chosen = new Int8Array(count.length).fill(-1);
  count[0] = 0; area[0] = 0;
  for (let mask = 0; mask <= fullMask; mask++) {
    if ((mask & ~fullMask) || !Number.isFinite(count[mask])) continue;
    for (let index = 0; index <= paletteCount; index++) {
      const bit = 1 << index;
      if (!(fullMask & bit) || (mask & bit)) continue;
      const rects = layer(mask, index);
      const next = mask | bit;
      const nextCount = count[mask] + rects.length;
      const nextArea = area[mask] + rects.reduce((sum, r) => sum + r[2] * r[3], 0);
      if (nextCount < count[next] || (nextCount === count[next] && nextArea < area[next])) {
        count[next] = nextCount; area[next] = nextArea;
        previous[next] = mask; chosen[next] = index;
      }
    }
  }
  const sequence = [];
  for (let mask = fullMask; mask; mask = previous[mask]) sequence.push(layer(previous[mask], chosen[mask]));
  const layered = sequence.reverse().flat();
  const replay = new Int8Array(pixels.length).fill(-1);
  const result = [];
  for (const r of layered) {
    const [x, y, w, h, c] = r;
    let changes = false;
    for (let yy = y; yy < y + h; yy++) for (let xx = x; xx < x + w; xx++) {
      const i = yy * width + xx;
      if (replay[i] !== c) changes = true;
      replay[i] = c;
    }
    if (changes) result.push(r);
  }
  for (let i = 0; i < replay.length; i++) if (replay[i] !== pixels[i]) {
    throw new Error(`Internal encoding mismatch at pixel ${i}`);
  }
  return result;
}

module.exports = {encode};
