import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";
const KEY = "litetraffic-theme";

function stored(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

// One store for the whole page, so every theme control (header, sidebar footer) shows the same choice.
let current: Theme = stored();
const listeners = new Set<() => void>();

function apply(theme: Theme) {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  if (theme !== "system") root.classList.add(theme);
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // ponytail: storage blocked (private mode); the choice lasts for this page only.
  }
}

export function setTheme(theme: Theme) {
  current = theme;
  apply(theme);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tokens follow the OS by default; `.light` / `.dark` on <html> force a theme. */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, () => current);
  return [theme, setTheme] as const;
}

apply(current);
