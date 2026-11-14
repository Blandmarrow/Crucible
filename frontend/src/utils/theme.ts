import { THEME_KEY } from "../constants/storage";

/** What the user picked in Settings → UI Behavior → Appearance. */
export type ThemePref = "dark" | "light" | "system";
/** What is actually on screen: `system` resolved through the OS setting. */
export type Theme = "dark" | "light";

// The browser chrome colour (`<meta name="theme-color">`) for each theme: the
// page background, so the tab strip or mobile status bar blends into the app.
// Mirrors --bg in index.css.
const THEME_COLOR: Record<Theme, string> = { dark: "#07090b", light: "#d5d8d7" };

const LIGHT_QUERY = "(prefers-color-scheme: light)";

export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "system" ? v : "dark";
  } catch {
    return "dark";
  }
}

export function resolveTheme(pref: ThemePref): Theme {
  if (pref !== "system") return pref;
  return window.matchMedia?.(LIGHT_QUERY).matches ? "light" : "dark";
}

/**
 * Put the theme on the page: `data-theme` on <html>, which the token blocks in
 * index.css key on, and the matching browser chrome colour. The inline script
 * in index.html does the same thing before first paint; this keeps it current
 * after a change in Settings or in the OS setting.
 */
export function applyTheme(pref: ThemePref = getThemePref()): void {
  const theme = resolveTheme(pref);
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
}

export function setThemePref(pref: ThemePref): void {
  try {
    localStorage.setItem(THEME_KEY, pref);
  } catch {
    // Storage blocked: the choice still applies for this page view.
  }
  applyTheme(pref);
}

/**
 * Follow the OS light/dark setting while the choice is "System", and a choice
 * made in another tab of the same origin. Installed once, from main.tsx.
 */
export function watchTheme(): void {
  window.matchMedia?.(LIGHT_QUERY).addEventListener?.("change", () => {
    if (getThemePref() === "system") applyTheme("system");
  });
  window.addEventListener("storage", (e) => {
    // A null key is localStorage.clear() in another tab: back to the default.
    if (e.key === THEME_KEY || e.key === null) applyTheme();
  });
}
