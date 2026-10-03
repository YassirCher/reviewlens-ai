// Next's lint plugin uses only globSync(pattern, { onlyDirectories: true }).
// Preserve fast-glob's directory semantics without its unpatched braces chain.
// eslint-disable-next-line @typescript-eslint/no-require-imports -- Next's CJS lint plugin requires this adapter.
const { globSync } = require("tinyglobby");
// eslint-disable-next-line @typescript-eslint/no-require-imports -- This is a CommonJS module.
const { isAbsolute } = require("node:path");

exports.globSync = (patterns, options = {}) => {
  const absolute = options.absolute ?? (typeof patterns === "string" ? isAbsolute(patterns) : patterns.every(isAbsolute));
  return globSync(patterns, { ...options, absolute, expandDirectories: false })
    .map(value => value.replace(/\/$/, ""));
};
