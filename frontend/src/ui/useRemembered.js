import { useState } from "react";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readRemembered(key, fallback = null) {
  try {
    const v = storage()?.getItem(key);
    return v === null || v === undefined ? fallback : v;
  } catch {
    return fallback;
  }
}

export function writeRemembered(key, value) {
  try {
    if (value === null || value === undefined) storage()?.removeItem(key);
    else storage()?.setItem(key, String(value));
  } catch {
    // storage off: not remembered, still works
  }
}

// [value, setValue] remembered on this browser under `key` ("kc.tab.<page>").
export default function useRemembered(key, fallback) {
  const [value, setValue] = useState(() => readRemembered(key, fallback));
  function set(next) {
    setValue(next);
    writeRemembered(key, next);
  }
  return [value, set];
}
