import { useEffect, useState } from "react";

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

/** Tokens follow the OS by default; `.light` / `.dark` on <html> force a theme. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(stored);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("light", "dark");
    if (theme !== "system") root.classList.add(theme);
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      // ponytail: storage blocked (private mode); the choice lasts for this page only.
    }
  }, [theme]);

  return [theme, setTheme] as const;
}
