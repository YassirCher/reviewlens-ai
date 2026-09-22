"use client";

import React, { createContext, useContext, useEffect, useState } from "react";

export type Theme = "dark" | "light" | "system";

interface ThemeContextType {
  theme: Theme;
  resolvedTheme: "dark" | "light";
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

const STORAGE_KEY = "reviewlens-theme";

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "system";
  try {
    const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
    if (stored === "dark" || stored === "light" || stored === "system") {
      return stored;
    }
  } catch {
    // Storage unavailable or private browsing
  }
  return "system";
}

function getInitialResolvedTheme(theme: Theme): "dark" | "light" {
  if (typeof window === "undefined") return "dark";
  if (theme === "dark" || theme === "light") return theme;
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "dark";
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const [resolvedTheme, setResolvedTheme] = useState<"dark" | "light">(() => getInitialResolvedTheme(theme));

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

    function apply() {
      let active: "dark" | "light";
      if (theme === "system") {
        active = mediaQuery.matches ? "dark" : "light";
      } else {
        active = theme;
      }

      setResolvedTheme(active);
      document.documentElement.setAttribute("data-theme", active);
      document.documentElement.style.colorScheme = active;

      try {
        localStorage.setItem(STORAGE_KEY, theme);
      } catch {
        // Storage unavailable
      }
    }

    apply();

    function onMediaChange() {
      if (theme === "system") {
        apply();
      }
    }

    mediaQuery.addEventListener("change", onMediaChange);
    return () => mediaQuery.removeEventListener("change", onMediaChange);
  }, [theme]);

  function setTheme(next: Theme) {
    setThemeState(next);
  }

  function toggleTheme() {
    setThemeState((prev) => {
      const current = prev === "system" ? resolvedTheme : prev;
      return current === "dark" ? "light" : "dark";
    });
  }

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}
