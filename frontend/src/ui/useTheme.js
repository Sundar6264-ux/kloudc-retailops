import { useEffect, useState } from "react";
import { applyTheme, onThemeChange, readTheme, writeTheme } from "./theme.js";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

// [theme, setTheme]: "system" | "light" | "dark", remembered on this browser.
export default function useTheme() {
  const [theme, setThemeState] = useState(() => readTheme(storage()));
  useEffect(() => onThemeChange(setThemeState), []);
  function setTheme(next) {
    applyTheme(writeTheme(storage(), next));
  }
  return [theme, setTheme];
}
