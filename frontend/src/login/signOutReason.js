// Why this browser was signed out in the middle of a session (website ready
// A12): a master suspended the store. The sign-in page shows it once.
export const SIGNOUT_REASON_KEY = "kc.signout_reason";

// A suspended store's answer (403 store_suspended, maybe with the master's
// maintenance message) -> the message to show; anything else -> null.
export function suspendedMessage(err) {
  const body = err?.body && typeof err.body === "object" ? err.body : {};
  const code = body.error || body.detail?.error;
  if (err?.status !== 403 || code !== "store_suspended") return null;
  const msg = body.message || (typeof body.detail === "string" ? body.detail : "");
  return msg || "This store is paused. Contact support.";
}

export function rememberSignOutReason(storage, message) {
  try {
    if (message) storage?.setItem(SIGNOUT_REASON_KEY, message);
  } catch {
    // not remembered: the sign-in page just shows no note
  }
}

// Read once, then forgotten.
export function takeSignOutReason(storage) {
  try {
    const v = storage?.getItem(SIGNOUT_REASON_KEY);
    storage?.removeItem(SIGNOUT_REASON_KEY);
    return v || null;
  } catch {
    return null;
  }
}
