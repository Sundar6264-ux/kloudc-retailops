// Session storage for the Capacitor-wrapped mobile app. The web app keeps
// using its existing httpOnly session cookie (see api.js's `request()`) -
// none of this runs there. The mobile app can't rely on that cookie the same
// way inside a Capacitor WebView, and biometric re-auth needs an explicit
// value to hand to Keychain/Keystore anyway, so it authenticates via
// POST /api/auth/mobile-login (see app/routers/auth.py) and sends the
// returned token back as `Authorization: Bearer <token>` instead.
import { Capacitor } from "@capacitor/core";
import { Preferences } from "@capacitor/preferences";

const TOKEN_KEY = "spicetown_mobile_token";

export function isNative() {
  return Capacitor.isNativePlatform();
}

// Cached in memory after the first read so every API call doesn't need an
// async Preferences round-trip - populated by restoreToken() once at app
// start and kept in sync by setToken()/clearToken() after that.
let cachedToken = null;

export async function restoreToken() {
  if (!isNative()) return null;
  const { value } = await Preferences.get({ key: TOKEN_KEY });
  cachedToken = value || null;
  return cachedToken;
}

export function getCachedToken() {
  return cachedToken;
}

export async function setToken(token) {
  cachedToken = token;
  if (isNative()) {
    await Preferences.set({ key: TOKEN_KEY, value: token });
  }
}

export async function clearToken() {
  cachedToken = null;
  if (isNative()) {
    await Preferences.remove({ key: TOKEN_KEY });
  }
}
