// Sales accuracy (Items Sold): the line shown when the POS's Analytics keys
// aren't set. The server's warning has no full stop ("... keys are
// missing"), so it ran into the next sentence ("missing Add them in ...").
export function missingKeysLine(warning, posName) {
  const first = String(warning || "").trim() || `${posName || "The POS"} Analytics keys are missing`;
  const sentence = /[.!?]$/.test(first) ? first : `${first}.`;
  return `${sentence} Add them in Setup › Integrations so late edits are caught. Until then the app re-checks the last 7 days once a day.`;
}
