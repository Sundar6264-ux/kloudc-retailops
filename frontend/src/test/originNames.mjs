// Names of the store this product was first built for, kept out of the
// product (KloudC RetailOps final pass). Only salted SHA-256 hashes are
// written here - the same list and rules as backend/tests/origin_names.py
// and the app's test/identity/origin_names.dart. A line is a hit when one
// word, or 2-3 neighbouring words joined (each 3+ letters, 6+ together),
// hashes to an EXACT entry, or a word starts or ends with a SUBSTRING entry.
// Node only (node:crypto): unit tests and the Playwright scripts.
import { createHash } from "node:crypto";

const SALT = "kloudc-retailops/origin-names/v1:";
const EXACT = new Set([
  "3f6f46dfbfd966b817866305f891fe11c0d658ddb507fb1e866bef705d410e5d",
  "1f343dfc57546a57e777995989f38fa50427f097f0d8cef50b0dafe500dbcd4b",
  "92dcbb59736cd79706bbaed3511de6215a22e222cb5f84b2b731d485afa8587a",
  "ebc5d0501d9177f52525d20d5df266cbdf86d33bbbc2b9be5a9783e445e8ddb4",
  "8937660eb6a6d1cd8be7b366a3226890c0b7edd6e5dff119c24e2534c9dc3ae5",
  "3504ffcb509fbbc51e49be7e2f078c0d8406525d441382be7328083849b38ee9",
  "14e80ffc703b6988de1b634a2d785293baff8faef3ad407d8bc765db8b8090ac",
  "1e071899c73f0fbe69de24511ec5517b84a8f22205952799f5155b3029f2c58e",
  "18ca962659e08e13477118dbb1fc7b2a87476780399b4a06a61ffc560c1b8dd7",
  "0f4a0e05db73c7f56685f848d5e1dc34921cd1d8264c0e20fd40fd5d4705daf9",
  "df019f9df438a0974afaa27200ee4d7629989160e5c58b971eeaf4c4802e519b",
  "73b81869a58fd7b80d854b2a1009d59dafb7aa01727ad1e8388dd6f818ba6cfd",
  "cd3cdc8755045be5456b98bdc5f15f06c49eb9befc67eb9a8c7e4f112a5a672f",
  "50c34d8ea9c37965db00dc0b992be77c79d9fabc3d63db7ddaf176a6bf2d6429",
]);
// [length, hash]
const SUBSTRING = [[9, "5012f887b1df1014f785c4dd27e2b386517cb85c657a80441b95c2b3db2a7ba4"]];

const h = (s) => createHash("sha256").update(SALT + s, "utf8").digest("hex");

function* candidates(words) {
  for (let i = 0; i < words.length; i++) {
    yield words[i];
    let joined = words[i];
    for (let j = i + 1; j < Math.min(i + 3, words.length); j++) {
      if (words[j].length < 3 || words[i].length < 3) break;
      joined += words[j];
      if (joined.length >= 6) yield joined;
    }
  }
}

export function lineHasOriginName(line) {
  // A backslash escape in source text ("\\nName") is a word break, not a letter.
  const words = String(line || "").replace(/\\[a-z]/g, " ").toLowerCase().match(/[a-z0-9]+/g) || [];
  for (const cand of candidates(words)) {
    if (cand.length >= 3 && cand.length <= 13 && EXACT.has(h(cand))) return true;
    for (const [len, hash] of SUBSTRING) {
      if (cand.length >= len && (h(cand.slice(0, len)) === hash || h(cand.slice(-len)) === hash)) return true;
    }
  }
  return false;
}

export function hasOriginName(text) {
  return String(text || "").split("\n").some(lineHasOriginName);
}

// The first line with a hit, or null.
export function firstOriginNameLine(text) {
  return String(text || "").split("\n").find(lineHasOriginName) ?? null;
}
