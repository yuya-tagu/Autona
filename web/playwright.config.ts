import {defineConfig, devices} from "@playwright/test";

// Browser E2E against the real headless app (ADR-005 S3d). The global setup
// starts `python main.py --headless` in a throwaway working directory
// (e2e/headless_server.py) and stops it afterwards; the tests reach it
// through e2e/server.ts. Needs web/build (`npm run build`) and the Python
// dependencies of the app. Windows only, like the headless smoke test.
//
// The specs live in e2e/, outside src/: react-scripts' jest only collects
// src/**, and pytest only tests/test_*.py.
export default defineConfig({
    testDir: "./e2e",
    // One server and one state for every test: run them one at a time.
    fullyParallel: false,
    workers: 1,
    forbidOnly: !!process.env.CI,
    retries: 0,
    timeout: 60_000,
    expect: {timeout: 10_000},
    reporter: [
        ["list"],
        ["html", {outputFolder: "playwright-report", open: "never"}],
    ],
    globalSetup: "./e2e/global-setup.ts",
    use: {
        // The Web UI picks its language from the browser.
        locale: "en-US",
        trace: "retain-on-failure",
    },
    projects: [
        {name: "chromium", use: {...devices["Desktop Chrome"], locale: "en-US"}},
    ],
});
