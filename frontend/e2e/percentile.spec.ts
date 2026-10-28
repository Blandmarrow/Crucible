import { test, expect } from '@playwright/test'
// The real module, not a copy: this spec runs in node with no page (it never
// touches the `page` fixture, so no browser is launched), and
// `npm run typecheck:e2e` is what keeps the cross-tree import compiling —
// the `gallery-restore.spec.ts` precedent.
import { percentileOf } from '../src/utils/percentile'

// `_style_quantiles(sorted([0.0, 0.5, 1.0]))`, hard-coded from the backend:
// nearest-rank scaled by n - 1 over 21 breakpoints, so with three scores q0..q25
// are 0, q30..q70 are 0.5 and q75..q100 are 1 (Python's round-half-to-even puts
// index 5 on 0 and index 15 on 1). `backend/tests/test_style_distribution_http.py`
// pins that the backend produces repeated breakpoints of this shape.
const TIED = [
  ...Array<number>(6).fill(0),
  ...Array<number>(9).fill(0.5),
  ...Array<number>(6).fill(1),
]

// GPT-019: a score equal to a flat run answers with the run's documented low
// edge. The loop used to walk to the first breakpoint *above* the score, so 0.5
// read as the 70th percentile instead of the 30th.
test('a score on a flat run of breakpoints lands on its low edge', () => {
  expect(TIED).toHaveLength(21)
  expect(percentileOf(0.5, TIED)).toBe(30)
})

test('scores between breakpoints interpolate, and the ends clamp', () => {
  // Halfway between q25 (0) and q30 (0.5), and between q70 (0.5) and q75 (1).
  expect(percentileOf(0.25, TIED)).toBe(27.5)
  expect(percentileOf(0.75, TIED)).toBe(72.5)
  // The clamp wins over the low-edge rule at both ends: the minimum is the 0th
  // percentile and the maximum the 100th, even though each fills a flat run.
  expect(percentileOf(0, TIED)).toBe(0)
  expect(percentileOf(1, TIED)).toBe(100)
  expect(percentileOf(-0.2, TIED)).toBe(0)
  expect(percentileOf(1.3, TIED)).toBe(100)
})

test('no meter where there is no ranking to report', () => {
  // One scored image (or every score identical): 21 equal breakpoints.
  expect(percentileOf(0.71, Array<number>(21).fill(0.71))).toBeNull()
  // Unscored image, payload not loaded.
  expect(percentileOf(null, TIED)).toBeNull()
  expect(percentileOf(0.5, undefined)).toBeNull()
  expect(percentileOf(0.5, [0.5])).toBeNull()
})
