// @ts-expect-error TypeScript has no type for a text import
import styles from '../styles.css' with { loader: 'text' };

// The stylesheet's colour tokens (spec: UI, colours). Text colours must reach WCAG AA on every background.
const TEXT = [
  '--text',
  '--muted',
  '--accent',
  '--llm',
  '--harness',
  '--tool',
  '--amber',
  '--orange',
  '--red',
  '--blue',
  '--green',
];
const BACKGROUNDS = ['--panel', '--bg'];
// Role and attention colours, which the stylesheet also mixes into the panel as a tint behind text.
const TINTS = ['--llm', '--harness', '--tool', '--amber', '--orange', '--red', '--blue', '--green'];
const CSS = styles as string;

function tokens(block: string | undefined): Record<string, string> {
  return Object.fromEntries(
    [...(block ?? '').matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
  );
}

/** The first `:root` block is the light theme; the `:root` inside the dark media query is the dark theme. */
const THEMES = {
  light: tokens(CSS.match(/:root\s*\{([^}]*)\}/)?.[1]),
  dark: tokens(CSS.match(/@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{([^}]*)\}/)?.[1]),
};

/** Every tint in the stylesheet, as `color-mix(in srgb, var(--x) N%, var(--panel))`. */
const TINTS_IN_CSS = [
  ...CSS.matchAll(/color-mix\(in srgb, var\(--[\w-]+\) (\d+)%, var\(--panel\)\)/g),
];
const TINT_PERCENT = Math.max(...TINTS_IN_CSS.map((m) => Number(m[1])));

type Rgb = number[];

function rgb(hex: string): Rgb {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
}

/** `color-mix(in srgb, colour p%, base)`: a straight mix of the encoded channels. */
function mix(colour: Rgb, base: Rgb, percent: number): Rgb {
  return colour.map((c, i) => (c * percent + base[i] * (100 - percent)) / 100);
}

/** WCAG 2.2 relative luminance. */
function luminance(colour: Rgb): number {
  const [r, g, b] = colour.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: Rgb, b: Rgb): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

describe('palette', () => {
  it('computes the WCAG contrast ratio and the srgb mix', () => {
    expect(contrast(rgb('#000000'), rgb('#ffffff'))).toBeCloseTo(21, 5);
    expect(contrast(rgb('#777777'), rgb('#ffffff'))).toBeCloseTo(4.48, 2);
    expect(mix(rgb('#000000'), rgb('#ffffff'), 25)).toEqual([0.75, 0.75, 0.75]);
  });

  it('every colour token has a light and a dark value', () => {
    for (const theme of Object.values(THEMES)) {
      for (const token of [...TEXT, ...BACKGROUNDS]) {
        expect(theme[token], token).toMatch(/^#[0-9a-f]{6}$/i);
      }
    }
  });

  it('every text colour reaches 4.5:1 on the panel and the page background in both themes', () => {
    const low = Object.entries(THEMES)
      .flatMap(([name, theme]) =>
        TEXT.flatMap((text) =>
          BACKGROUNDS.map((bg) => ({
            name,
            text,
            bg,
            ratio: contrast(rgb(theme[text]), rgb(theme[bg])),
          })),
        ),
      )
      .filter((pair) => pair.ratio < 4.5);
    expect(low).toEqual([]);
  });

  it('every text colour reaches 4.5:1 on the strongest tint of every role and attention colour', () => {
    // Every color-mix has the checked form: a tint in any other form must be looked at, not skipped.
    expect(TINTS_IN_CSS.length).toBe(CSS.match(/color-mix\(/g)?.length);
    expect(TINT_PERCENT).toBeGreaterThan(0);
    const low = Object.entries(THEMES)
      .flatMap(([name, theme]) =>
        TEXT.flatMap((text) =>
          TINTS.map((tint) => ({
            name,
            text,
            tint,
            ratio: contrast(
              rgb(theme[text]),
              mix(rgb(theme[tint]), rgb(theme['--panel']), TINT_PERCENT),
            ),
          })),
        ),
      )
      .filter((pair) => pair.ratio < 4.5);
    expect(low).toEqual([]);
  });
});
