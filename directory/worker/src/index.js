// Worker entry. `data.json` is generated (gitignored):
//   cd backend && python -m app.directory.build_static \
//     --stores ../directory/stores.json --worker-data ../directory/worker/src/data.json
// Wrangler bundles the JSON into the Worker; nothing is read at runtime.
import data from "./data.json";
import { createDirectory } from "./directory.js";

const directory = createDirectory(data);

export default {
  fetch(request, env, ctx) {
    return directory.fetch(request, env, ctx);
  },
};
