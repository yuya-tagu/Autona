import {expect, test} from "@playwright/test";
import {serverUrl} from "./server";

// The plugins page's routes (ADR-009 D4) on the headless app: the list is
// THIS_PC (read without the shell's secret, in the v1 envelope), every other
// plugin route is SHELL (403 to a browser: there is no desktop here to give
// the secret). The page's own flow is jest's (PluginsPage.test.js); the
// routes' bodies are tests/test_plugin_api.py's.

test("the list reads as THIS_PC, in the envelope", async ({request}) => {
    const response = await request.get(serverUrl("/api/v1/plugins"));
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.ok).toBe(true);
    expect(Array.isArray(body.data)).toBe(true);
});

test("a SHELL route answers 403 without the shell's secret", async ({request}) => {
    const base = serverUrl("/api/v1/plugins");
    expect((await request.get(`${base}/taguyan.vodsplit`)).status()).toBe(403);
    expect((await request.get(`${base}/dev`)).status()).toBe(403);
    expect((await request.get(`${base}/taguyan.vodsplit/runs`)).status())
        .toBe(403);
});
