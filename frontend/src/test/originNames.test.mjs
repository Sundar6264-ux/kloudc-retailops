// The hashed origin-name matcher (src/test/originNames.mjs).
//   node --test src/test/originNames.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { hasOriginName } from "./originNames.mjs";

test("neutral sample names are never a hit", () => {
  for (const ok of ["Main Store", "Market Kitchen", "Windsor, CT 06095", "(860) 555-0142", "KloudC RetailOps", "Sasha", "store's AI"]) {
    assert.equal(hasOriginName(ok), false, ok);
  }
});

test("a phone number is joined across its punctuation", () => {
  const phone = ["86" + "0", "23" + "7", "42" + "80"];
  assert.equal(hasOriginName(`call ${phone.join("-")}`), true);
  assert.equal(hasOriginName(`(${phone[0]}) ${phone[1]}-${phone[2]}`), true);
});
