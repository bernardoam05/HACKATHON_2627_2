// Owner: Team B. Debug mode: shows agent telemetry (response time, failed models) when the page is
// opened with ?debug (also ?debug=1 / true / on), either before the hash (/?debug#chat) or after it
// (/#chat?debug=true). Stateless: no debug parameter in the URL, no telemetry.

const OFF = new Set(["0", "false", "off", "no"]);

/** Query string that follows the tab in the hash, e.g. "?debug=true" in "#chat?debug=true". */
export function hashQuery(hash = location.hash): string {
  const i = hash.indexOf("?");
  return i === -1 ? "" : hash.slice(i);
}

/** Read once at load: changing the URL's query string reloads the page anyway. */
export const DEBUG: boolean = (() => {
  const params = [new URLSearchParams(location.search), new URLSearchParams(hashQuery())];
  const v = params.map((p) => p.get("debug")).find((x) => x !== null);
  return v !== undefined && v !== null && !OFF.has(v.toLowerCase());
})();
