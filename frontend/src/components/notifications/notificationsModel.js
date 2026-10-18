// Pure helpers for the Notifications page (pages/NotificationSettingsPage.jsx),
// kept here so node --test can check them. Shapes: docs/BATCH_OCT15_CONTRACT.md
// "As built - NOTIFICATIONS (server)" - GET/PUT /api/notifications/prefs and
// (admins) /api/notifications/defaults.

// The server sends `groups`; these are only the fallback for an older server
// (items without `group`) and the order to show them in, same as the app.
export const GROUP_ORDER = ["needs_action", "team", "daily_summary", "admin"];
const FALLBACK_GROUPS = {
  needs_action: { label: "Needs action", description: "Something for you to do now." },
  team: { label: "Team", description: "People talking to you: chat, @mentions, announcements." },
  daily_summary: { label: "Daily summary", description: "The 9 AM Morning summary, or any of its parts as a separate push." },
  admin: { label: "Admin", description: "Store-level alerts for admins." },
  other: { label: "Alerts", description: "" },
};

// [{ key, label, description, items }] in the server's group order, empty
// groups left out. An item without a group (older server) lands in "Alerts".
export function groupItems(payload) {
  const items = payload?.items || [];
  const given = payload?.groups || [];
  const order = [...given.map((g) => g.key), ...GROUP_ORDER.filter((k) => !given.some((g) => g.key === k)), "other"];
  const meta = (key) => given.find((g) => g.key === key) || { key, ...(FALLBACK_GROUPS[key] || { label: key, description: "" }) };
  const known = new Set(order);
  return order
    .map((key) => {
      const inGroup = items.filter((i) => (i.group && known.has(i.group) ? i.group : "other") === key);
      const m = meta(key);
      return { key, label: m.label, description: m.description || "", items: inGroup };
    })
    .filter((g) => g.items.length > 0);
}

// Switched differently from what they'd get by default (their own choice).
export function isChanged(item) {
  return !!item.explicit && "default" in item && !!item.enabled !== !!item.default;
}

export function changedCount(items) {
  return (items || []).filter(isChanged).length;
}

// "Reset to recommended" is worth offering once they've chosen anything (a
// choice that matches today's default still pins it if defaults change).
export function hasOwnChoices(items) {
  return (items || []).some((i) => !!i.explicit);
}

// Essential alerts ask before being switched off ("Are you sure?").
export function needsConfirm(item, next) {
  return !!item.essential && !!item.enabled && !next;
}

export function confirmOffText(item) {
  return `Turn off "${item.label}"? ${item.description || ""}`.trim() + " You can switch it back on here any time.";
}

// The switch flips straight away; the server's answer replaces it.
export function applyLocal(payload, key, enabled) {
  return {
    ...payload,
    items: (payload?.items || []).map((i) => (i.key === key ? { ...i, enabled: !!enabled, explicit: true } : i)),
  };
}

export function recommendedWord(on) {
  return on ? "On" : "Off";
}

// --- Store defaults (admins) -------------------------------------------------

export const ROLE_LABELS = { staff: "Staff", admin: "Admins" };

// The select's value for one alert and role: "recommended" (no store
// override), "on" or "off".
export function storeChoice(item, role) {
  const v = item?.store?.[role];
  if (v === null || v === undefined) return "recommended";
  return v ? "on" : "off";
}

// A store default going off for an essential alert asks first (as the app).
export function defaultNeedsConfirm(item, role, choice) {
  if (!item?.essential) return false;
  const was = item?.effective?.[role];
  return choice === "off" || (choice === "recommended" && was && !item?.catalog?.[role]);
}

// What PUT /defaults takes for a select value.
export function choiceToEnabled(choice) {
  if (choice === "on") return true;
  if (choice === "off") return false;
  return null;
}

export function overrideCount(state) {
  let n = 0;
  for (const item of state?.items || []) {
    for (const role of item.roles || []) {
      if (storeChoice(item, role) !== "recommended") n += 1;
    }
  }
  return n;
}

// The defaults list in the same groups as the switches above.
export function groupDefaults(state) {
  return groupItems({ items: state?.items || [], groups: state?.groups || [] });
}

// "Recommended (On)" / "On" / "Off" for the select.
export function choiceOptions(item, role) {
  const rec = recommendedWord(!!item?.catalog?.[role]);
  return [
    { value: "recommended", label: `Recommended (${rec})` },
    { value: "on", label: "On" },
    { value: "off", label: "Off" },
  ];
}
