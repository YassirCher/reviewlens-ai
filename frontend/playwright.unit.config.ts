import { defineConfig } from "@playwright/test";

export default defineConfig({ testDir: "./tests/unit", workers: 1, timeout: 10_000 });
