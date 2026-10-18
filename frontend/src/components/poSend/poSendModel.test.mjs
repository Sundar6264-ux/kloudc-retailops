// node --test src/components/poSend/poSendModel.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import {
  addCc,
  ccProblem,
  cleanNote,
  contactForm,
  effectiveCc,
  parseEmailList,
  removeCc,
  contactPayload,
  contactProblem,
  contactsLinkFor,
  emailOptions,
  gmailSearch,
  groupContacts,
  isNoAnswer,
  newIdempotencyKey,
  noteTooLong,
  renderBody,
  sendBlocker,
  sentNotice,
  statusLabel,
  toggleEmail,
  vendorsMissingEmail,
} from "./poSendModel.js";

const M = "[[NOTE]]";
const TEMPLATE = `Hello X team,\n\nPlease find attached.\n\n${M}\n\nPlease reply.\n\nThank you`;

test("renderBody matches the server rule", () => {
  assert.equal(renderBody(TEMPLATE, M, ""), "Hello X team,\n\nPlease find attached.\n\nPlease reply.\n\nThank you");
  assert.equal(renderBody(TEMPLATE, M, "   \n "), renderBody(TEMPLATE, M, ""));
  assert.equal(
    renderBody(TEMPLATE, M, "Deliver Tuesday.  \r\nThanks"),
    "Hello X team,\n\nPlease find attached.\n\nDeliver Tuesday.\nThanks\n\nPlease reply.\n\nThank you",
  );
  assert.equal(renderBody("", M, "x"), "");
  assert.equal(cleanNote(null), "");
  assert.equal(noteTooLong("abc", 2), true);
  assert.equal(noteTooLong("ab ", 2), false);
});

test("idempotency keys are unique and server-safe", () => {
  const a = newIdempotencyKey();
  const b = newIdempotencyKey();
  assert.notEqual(a, b);
  assert.match(a, /^[A-Za-z0-9_-]{8,100}$/);
});

const PREVIEW = {
  supplier: "Raja Foods",
  item_count: 2,
  email_configured: true,
  max_note: 10,
  recipients: ["sales@raja.test"],
  contacts: [
    { email: "sales@raja.test", contact_name: "Ravi", send_po: true },
    { email: "SALES@raja.test", contact_name: "dup", send_po: true },
    { email: "office@raja.test", contact_name: null, send_po: false },
    { email: null, whatsapp: "+1860", send_po: true },
  ],
};

test("email options and ticking", () => {
  const opts = emailOptions(PREVIEW);
  assert.deepEqual(
    opts.map((o) => [o.email, o.ticked, o.onPos]),
    [
      ["sales@raja.test", true, true],
      ["office@raja.test", false, false],
    ],
  );
  assert.deepEqual(toggleEmail(["a@x.test"], "A@x.test"), []);
  assert.deepEqual(toggleEmail([], "a@x.test"), ["a@x.test"]);
});

test("sendBlocker explains what stops Send", () => {
  assert.equal(sendBlocker(PREVIEW, ["sales@raja.test"], ""), null);
  assert.match(sendBlocker(PREVIEW, [], ""), /Tick at least one/);
  assert.match(sendBlocker(PREVIEW, ["sales@raja.test"], "x".repeat(11)), /too long/);
  assert.match(sendBlocker({ ...PREVIEW, item_count: 0 }, ["a"], ""), /nothing to put/);
  assert.match(sendBlocker({ ...PREVIEW, email_configured: false }, ["a"], ""), /isn't set up/);
  assert.match(sendBlocker({ ...PREVIEW, contacts: [] }, [], ""), /No email on file for Raja Foods/);
  assert.match(sendBlocker(PREVIEW, ["a"], "", { demo: true }), /demo/);
  assert.equal(sendBlocker(null, [], ""), "Loading…");
});

test("sentNotice says where it went and whether it was recorded", () => {
  const base = {
    supplier: "Raja Foods",
    status: "sent",
    email_status: "sent",
    email_to: ["a@x.test", "b@x.test", "c@x.test"],
    placed_order_id: 4,
  };
  assert.equal(
    sentNotice(base),
    "Emailed Raja Foods's PO to a@x.test, b@x.test and c@x.test. Recorded as placed and taken out of the cart.",
  );
  assert.match(sentNotice({ ...base, placed_order_id: null }), /press Order placed/);
  assert.match(
    sentNotice({ ...base, status: "partly_sent", whatsapp_status: "failed", whatsapp_error: "402" }),
    /WhatsApp: 402/,
  );
  assert.equal(sentNotice(null), "");
});

test("small helpers", () => {
  assert.equal(gmailSearch("<abc@store.test>"), "rfc822msgid:abc@store.test");
  assert.equal(gmailSearch(null), "");
  assert.equal(statusLabel("uncertain"), "Not confirmed");
  assert.equal(statusLabel("weird"), "weird");
  assert.equal(isNoAnswer(new Error("Failed to fetch")), true);
  assert.equal(isNoAnswer(Object.assign(new Error("x"), { status: 502 })), false);
  assert.equal(contactsLinkFor("A & B"), "/vendor-contacts?vendor=A%20%26%20B");
  assert.equal(contactsLinkFor(""), "/vendor-contacts");
});

test("contacts grouping, search and missing-email vendors", () => {
  const items = [
    { vendor: "raja foods", contact_name: "Ravi", email: "r@x.test" },
    { vendor: "Chetak", contact_name: "Office", whatsapp: "+1860" },
    { vendor: "Raja Foods ", contact_name: "Office", email: "o@x.test" },
  ];
  const groups = groupContacts(items);
  assert.deepEqual(
    groups.map((g) => [g.vendor, g.contacts.length]),
    [
      ["Chetak", 1],
      ["raja foods", 2],
    ],
  );
  assert.deepEqual(
    groupContacts(items, "o@x").map((g) => g.contacts.map((c) => c.email)),
    [["o@x.test"]],
  );
  assert.deepEqual(vendorsMissingEmail(["Chetak", "Raja Foods", "KP Produce", "kp produce"], items), [
    "Chetak",
    "KP Produce",
  ]);
});

test("contact form checks and payload", () => {
  const form = { vendor: " Raja ", contact_name: " ", email: " a@b.test ", whatsapp: "", notes: "", send_po: true };
  assert.equal(contactProblem(form), null);
  assert.deepEqual(contactPayload(form), {
    vendor: "Raja",
    contact_name: null,
    email: "a@b.test",
    whatsapp: null,
    notes: null,
    send_po: true,
    cc: [],
  });
  assert.match(contactProblem({ ...form, vendor: "" }), /vendor/);
  assert.match(contactProblem({ ...form, email: "", whatsapp: "" }), /email or a WhatsApp/);
  assert.match(contactProblem({ ...form, email: "nope" }), /doesn't look right/);
});

test("CC helpers (W2): parse, add, dedupe, limit, To overlap", () => {
  assert.deepEqual(parseEmailList(" a@x.test, b@x.test;c@x.test\nd@x.test "), [
    "a@x.test",
    "b@x.test",
    "c@x.test",
    "d@x.test",
  ]);
  let r = addCc([], "Owner@ST.test, owner@st.test, sales@raja.test", ["sales@raja.test"]);
  assert.deepEqual(r, { list: ["Owner@ST.test"], error: null });
  r = addCc(r.list, "nope");
  assert.match(r.error, /isn't an email/);
  assert.deepEqual(r.list, ["Owner@ST.test"]);
  const ten = Array.from({ length: 10 }, (_, i) => `p${i}@x.test`);
  r = addCc(ten, "extra@x.test");
  assert.match(r.error, /At most 10/);
  assert.equal(r.list.length, 10);
  assert.deepEqual(removeCc(["A@x.test", "b@x.test"], "a@X.test"), ["b@x.test"]);
  assert.deepEqual(effectiveCc(["a@x.test", "b@x.test"], ["B@x.test"]), ["a@x.test"]);
  assert.equal(ccProblem(["a@x.test"]), null);
  assert.match(ccProblem(["bad"]), /isn't an email/);
  assert.match(ccProblem([...ten, "q@x.test"]), /At most 10/);
});

test("CC in the contact form, Send blocker and the sent notice", () => {
  const form = contactForm({ vendor: "Raja", email: "a@b.test", cc: ["o@st.test", "ap@raja.test"] });
  assert.equal(form.cc, "o@st.test, ap@raja.test");
  assert.deepEqual(contactPayload(form).cc, ["o@st.test", "ap@raja.test"]);
  assert.match(contactProblem({ ...form, cc: "o@st.test, broken" }), /CC: "broken"/);
  const many = Array.from({ length: 11 }, (_, i) => `p${i}@x.test`).join(",");
  assert.match(contactProblem({ ...form, cc: many }), /at most 10/);

  const preview = {
    supplier: "Raja",
    item_count: 2,
    email_configured: true,
    contacts: [{ email: "a@b.test", send_po: true }],
    recipients: ["a@b.test"],
    max_note: 1000,
    max_cc: 10,
  };
  assert.equal(sendBlocker(preview, ["a@b.test"], "", { cc: ["o@st.test"] }), null);
  assert.match(sendBlocker(preview, ["a@b.test"], "", { cc: ["x"] }), /isn't an email/);

  const send = {
    supplier: "Raja",
    status: "sent",
    email_status: "sent",
    email_to: ["a@b.test"],
    email_cc: ["o@st.test", "ap@raja.test"],
    placed_order_id: 4,
  };
  assert.match(sentNotice(send), /CC: o@st.test and ap@raja.test\./);
  assert.doesNotMatch(sentNotice({ ...send, email_cc: [] }), /CC/);
});
