'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {encode} = require('./rectangle-encoder.cjs');

function replay(rectangles, width, height, paletteCount) {
  const image = Array(width * height).fill(-1);
  for (const rectangle of rectangles) {
    assert.equal(rectangle.length, 5);
    assert.ok(rectangle.every(Number.isInteger));
    const [x, y, w, h, color] = rectangle;
    assert.ok(x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= width && y + h <= height);
    assert.ok(color >= -1 && color < paletteCount);
    for (let yy = y; yy < y + h; yy++) for (let xx = x; xx < x + w; xx++) {
      image[yy * width + xx] = color;
    }
  }
  return image;
}

const cases = [
  {name: 'transparent singleton', w: 1, h: 1, n: 1, pixels: [-1]},
  {name: 'opaque singleton', w: 1, h: 1, n: 1, pixels: [0]},
  {name: 'transparent cross repaints previous colour', w: 9, h: 9, n: 2,
    pixels: Array.from({length: 81}, (_, i) => Math.floor(i / 9) === 4 || i % 9 === 4 ? -1 : 0)},
  {name: 'three-colour stripes', w: 6, h: 6, n: 3, pixels: Array.from({length: 36}, (_, i) => i % 3)},
];
let seed = 123456;
function random() { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 2 ** 32; }
for (let index = 0; index < 30; index++) {
  const w = 2 + Math.floor(random() * 9), h = 2 + Math.floor(random() * 9), n = 1 + Math.floor(random() * 8);
  cases.push({name: `seeded mixed colours ${index + 1}`, w, h, n,
    pixels: Array.from({length: w * h}, () => Math.floor(random() * (n + 1)) - 1)});
}
for (const image of cases) test(image.name, () => {
  const untouched = image.pixels.slice();
  const rectangles = encode(image.pixels, image.w, image.h, image.n);
  assert.deepEqual(replay(rectangles, image.w, image.h, image.n), image.pixels);
  assert.deepEqual(image.pixels, untouched, 'the source buffer must remain unchanged');
  if (image.name === 'transparent cross repaints previous colour') {
    assert.equal(rectangles.length, 3, 'layering should beat the four nonoverlapping quadrants');
    assert.ok(rectangles.some(r => r[4] === -1), 'background restoration is an actual paint operation');
  }
});
