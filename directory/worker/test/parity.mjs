// Replays the requests backend/tests/test_directory.py sent to the Python
// service against the Worker and checks status, body and headers match.
// Usage: node test/parity.mjs <data.json> <cases.json>
import { readFileSync } from "node:fs";
import { createDirectory } from "../src/directory.js";

const [dataPath, casesPath] = process.argv.slice(2);
const data = JSON.parse(readFileSync(dataPath, "utf8"));
const cases = JSON.parse(readFileSync(casesPath, "utf8"));

let t = cases.start;
const directory = createDirectory(data, { now: () => t });
const failures = [];
let requests = 0;

for (const step of cases.steps) {
  if ("advance" in step) {
    t += step.advance;
    continue;
  }
  requests += 1;
  const request = new Request(`https://directory.storeops.invalid${step.path}`, { headers: step.headers });
  const response = await directory.fetch(request, {}, {});
  const body = await response.text();
  const where = `#${requests} ${step.path}`;
  if (response.status !== step.status) failures.push(`${where}: status ${response.status} != ${step.status}`);
  if (body !== step.body) failures.push(`${where}: body differs\n  worker:  ${body}\n  service: ${step.body}`);
  for (const [name, want] of Object.entries(step.expect_headers)) {
    const got = response.headers.get(name);
    if (got !== want) failures.push(`${where}: header ${name} ${JSON.stringify(got)} != ${JSON.stringify(want)}`);
  }
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(`parity ok: ${requests} requests`);
