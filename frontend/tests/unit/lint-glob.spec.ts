import { expect, test } from "@playwright/test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";

test("Next lint root discovery preserves exact directories, wildcards, arrays, and brace groups", () => {
  const loader = createRequire(require.resolve("@next/eslint-plugin-next"));
  expect(loader("fast-glob/package.json").description).toBe("ReviewLens private adapter for Next lint root discovery");
  const { getRootDirs } = loader("./utils/get-root-dirs.js");
  const root = mkdtempSync(join(tmpdir(), "reviewlens-lint-glob-"));
  try {
    for (const directory of ["alpha", "beta", "alpha/nested"]) mkdirSync(join(root, directory), { recursive: true });
    writeFileSync(join(root, "plain.txt"), "fixture");
    const normalized = root.replaceAll("\\", "/");
    const discover = (rootDir: string | string[]) => getRootDirs({ cwd: root, settings: { next: { rootDir } } }).map((value: string) => value.replaceAll("\\", "/")).sort();
    expect(discover(`${normalized}/alpha`)).toEqual([`${normalized}/alpha`]);
    expect(discover(`${normalized}/*`)).toEqual([`${normalized}/alpha`, `${normalized}/beta`]);
    expect(discover(`${normalized}/{alpha,beta}`)).toEqual([`${normalized}/alpha`, `${normalized}/beta`]);
    expect(discover([`${normalized}/alpha`, `${normalized}/beta`])).toEqual([`${normalized}/alpha`, `${normalized}/beta`]);
    expect(getRootDirs({ cwd: root, settings: {} })).toEqual([root]);
  } finally { rmSync(root, { recursive: true }); }
});
