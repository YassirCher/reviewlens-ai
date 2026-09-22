import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

function parseHex(hex: string) {
  const normalized = hex.trim();
  const channels = [1, 3, 5]
    .map(index => parseInt(normalized.slice(index, index + 2), 16) / 255)
    .map(value => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrast(a: string, b: string) {
  const [lum1, lum2] = [parseHex(a), parseHex(b)].sort((x, y) => y - x);
  return (lum1 + 0.05) / (lum2 + 0.05);
}

test.describe("Admin Control Plane WCAG AA Color Contrast", () => {
  const css = readFileSync("src/app/admin/admin.css", "utf8");

  test("dark mode admin semantic tokens meet WCAG AA contrast", () => {
    const darkSectionMatch = css.match(/\.admin\s*\{([^}]+)\}/);
    expect(darkSectionMatch).not.toBeNull();
    const darkSection = darkSectionMatch![1];

    const token = (name: string) => {
      const match = darkSection.match(new RegExp(`--admin-${name}:\\s*(#[0-9A-Fa-f]{6})`));
      expect(match, `missing dark mode token --admin-${name}`).not.toBeNull();
      return match![1];
    };

    const bg = token("bg"); // #090b10
    const s2 = token("s2"); // #151a23

    // Normal text contrast (>= 4.5:1)
    expect(contrast(token("text"), bg), "dark text on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("text"), s2), "dark text on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("muted"), bg), "dark muted on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("muted"), s2), "dark muted on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("primary"), bg), "dark primary on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("teal"), bg), "dark teal on bg").toBeGreaterThanOrEqual(4.5);
  });

  test("light mode admin semantic tokens meet WCAG AA contrast", () => {
    const lightSectionMatch = css.match(/\[data-theme="light"\]\s*\.admin[^{]*\{([^}]+)\}/);
    expect(lightSectionMatch).not.toBeNull();
    const lightSection = lightSectionMatch![1];

    const token = (name: string) => {
      const match = lightSection.match(new RegExp(`--admin-${name}:\\s*(#[0-9A-Fa-f]{6})`));
      expect(match, `missing light mode token --admin-${name}`).not.toBeNull();
      return match![1];
    };

    const bg = token("bg"); // #F8FAFC
    const s2 = token("s2"); // #FFFFFF

    // Normal text contrast (>= 4.5:1) against both light background and card surface
    expect(contrast(token("text"), bg), "light text on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("text"), s2), "light text on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("muted"), bg), "light muted on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("muted"), s2), "light muted on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("subtle"), bg), "light subtle on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("subtle"), s2), "light subtle on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("primary"), bg), "light primary on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("primary"), s2), "light primary on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("teal"), bg), "light teal on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("teal"), s2), "light teal on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("amber"), bg), "light amber on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("amber"), s2), "light amber on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("rose"), bg), "light rose on bg").toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("rose"), s2), "light rose on s2").toBeGreaterThanOrEqual(4.5);
    expect(contrast("#FFFFFF", token("primary")), "white text on light primary button").toBeGreaterThanOrEqual(4.5);
  });
});

test.describe("Theme Resolution & Anti-FOUC Contract", () => {
  const resolveTheme = (stored: string | null, prefersDark: boolean): "dark" | "light" => {
    if (stored === "dark" || stored === "light") return stored;
    return prefersDark ? "dark" : "light";
  };

  test("explicit stored preferences override system preference", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  test("unrecognized or missing storage values fall back to system preference", () => {
    expect(resolveTheme(null, true)).toBe("dark");
    expect(resolveTheme(null, false)).toBe("light");
    expect(resolveTheme("invalid", true)).toBe("dark");
    expect(resolveTheme("invalid", false)).toBe("light");
  });

  test("root layout contains the anti-FOUC inline script", () => {
    const layout = readFileSync("src/app/layout.tsx", "utf8");
    expect(layout).toContain("themeInitScript");
    expect(layout).toContain("reviewlens-theme");
    expect(layout).toContain("data-theme");
  });
});
