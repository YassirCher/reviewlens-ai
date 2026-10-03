/** A local alias enters the canonical site before any credentialed requests. */
export function canonicalLocalRedirect(requestUrl: string, configured = "http://localhost:3000"): string | null {
  let canonical: URL;
  let incoming: URL;
  try { canonical = new URL(configured); incoming = new URL(requestUrl); } catch { return null; }
  const local = new Set(["localhost", "127.0.0.1"]);
  if (!local.has(canonical.hostname) || !local.has(incoming.hostname) || canonical.username || canonical.password ||
      !["http:", "https:"].includes(canonical.protocol) || incoming.origin === canonical.origin) return null;
  canonical.pathname = incoming.pathname;
  canonical.search = incoming.search;
  canonical.hash = incoming.hash;
  return canonical.href;
}
