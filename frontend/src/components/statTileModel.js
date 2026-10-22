// Home stat tiles: the short line under a tile that couldn't load. A POS
// that isn't connected yet (the server's 503 says "... isn't connected
// yet ...") is not a failure to retry, so it says so; anything else stays
// "Couldn't load".
export function tileErrorText(message) {
  return /isn't connected yet/i.test(String(message || "")) ? "Not connected" : "Couldn't load";
}
