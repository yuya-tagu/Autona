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

test("the install and removal routes are SHELL too (403 headless)", async ({request}) => {
    const base = serverUrl("/api/v1/plugins");
    // The page hides its add / enable / remove controls here (no shell
    // access); the routes themselves refuse a browser all the same.
    expect((await request.post(`${base}/uploads`, {data: "PK"})).status())
        .toBe(403);
    expect((await request.post(`${base}/installs`,
                               {data: {upload_id: "x"}})).status()).toBe(403);
    expect((await request.get(`${base}/taguyan.vodsplit/removal`)).status())
        .toBe(403);
    expect((await request.post(`${base}/taguyan.vodsplit:disable`)).status())
        .toBe(403);
});
