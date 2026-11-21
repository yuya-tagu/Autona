import fs from "node:fs";
import path from "node:path";
import {APIRequestContext, expect, Page, test} from "@playwright/test";
import {appDir, keepLocal, serverUrl} from "./server";

// The check of a layout in the app (ADR-008 D3 / L3), on the headless app: a
// layout on layout/include/autona.js posts its errors and "ready" from the
// preview's frame (the preview listener's origin) to the page, which shows
// them as text in the check panel; a design that declares the contract but
// does not load autona.js is told so; one on globals.js alone says nothing
// and shows nothing; the sample switch draws layout/include/sample/; the
// folders the catalog does not list are shown with why. Checked through the
// DOM and the API, never screenshots.
//
// Not here: "copy for an AI" (the clipboard; DesignPane.test.js holds its
// text) and "make a new design" (SHELL: the desktop's shared view only).

const SELECTED = "/api/v1/layouts/selected/commentary";
// What the other specs expect the Commentary page to show.
const DEFAULT_DESIGN = "commentators";
const CHECK = "Layout check";
const UNLISTED = "Designs that could not be read";

const HEAD = `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>`;
const GLOBALS = `<script src="../include/globals.js"></script>`;
const AUTONA = `<script src="../include/autona.js"></script>`;

// layout/<name>/: files by name; a settings object is written as JSON.
const FOLDERS: Record<string, Record<string, string | object>> = {
    // On autona.js, with a listener that throws.
    commentators_e2e_broken: {
        "index.html": `${HEAD}${GLOBALS}${AUTONA}<script src="./index.js"></script></body></html>`,
        "index.js": "(function () {\n  Autona.onUpdate(function (view) {\n"
            + "    missingFunction(view);\n  });\n})();\n",
        "settings.json": {autona: {kind: "commentary", contract: 1,
                                   displayName: "E2E broken"}},
    },
    // Declares the contract, loads autona.js from a wrong path.
    commentators_e2e_unloaded: {
        "index.html": `${HEAD}${GLOBALS}<script src="../include/autona.min.js"></script></body></html>`,
        "settings.json": {autona: {kind: "commentary", contract: 1,
                                   displayName: "E2E unloaded"}},
    },
    // globals.js alone, no contract: as the shipped layouts are.
    commentators_e2e_legacy: {
        "index.html": `${HEAD}${GLOBALS}<script>LoadEverything();</script></body></html>`,
        "settings.json": {autona: {kind: "commentary",
                                   displayName: "E2E legacy"}},
    },
    // On autona.js and fine: writes the casters' names.
    commentators_e2e_fine: {
        "index.html": `${HEAD}<p id="names"></p>${GLOBALS}${AUTONA}<script>
(function () {
  Autona.onUpdate(function (view) {
    document.getElementById("names").textContent =
      view.commentary.map(function (caster) { return caster.name; }).join(",");
  });
})();
</script></body></html>`,
        "settings.json": {autona: {kind: "commentary", contract: 1,
                                   displayName: "E2E fine"}},
    },
    // Listed by its name, with a settings.json that is not JSON: warned.
    commentators_e2e_badjson: {
        "index.html": `${HEAD}${GLOBALS}<script>LoadEverything();</script></body></html>`,
        "settings.json": '{"autona": {"kind": "commentary",}}',
    },
    // Not listed: no page (a Commentary one by its name).
    commentators_e2e_pageless: {
        "settings.json": {autona: {kind: "commentary"}},
    },
    // Not listed: neither a block nor a prefix (every page's).
    e2e_loose: {"index.html": `${HEAD}</body></html>`},
};

let pageErrors: string[] = [];

function layoutRoot(): string {
    return path.join(appDir(), "layout");
}

async function select(request: APIRequestContext, id: string) {
    const response = await request.put(serverUrl(SELECTED), {data: {id}});
    expect(response.ok(), await response.text()).toBe(true);
}

async function openCommentary(page: Page, id: string) {
    await select(page.request, id);
    await page.goto(serverUrl("/web/commentary"));
    await expect(page.getByTitle("Preview", {exact: true})).toHaveAttribute(
        "src", new RegExp(`/layout/${id}/index\\.html`));
}

function check(page: Page) {
    return page.getByRole("region", {name: CHECK, exact: true});
}

test.beforeAll(() => {
    for (const [name, files] of Object.entries(FOLDERS)) {
        const folder = path.join(layoutRoot(), name);
        fs.mkdirSync(folder, {recursive: true});
        for (const [file, content] of Object.entries(files)) {
            fs.writeFileSync(path.join(folder, file), typeof content === "string"
                ? content : JSON.stringify(content), "utf8");
        }
    }
});

test.afterAll(async ({request}) => {
    await select(request, DEFAULT_DESIGN);
    for (const name of Object.keys(FOLDERS)) {
        fs.rmSync(path.join(layoutRoot(), name), {recursive: true, force: true});
    }
});

test.beforeEach(async ({page}) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    await keepLocal(page);
});

test.afterEach(() => {
    // The page's own errors (the layouts' are in their frames).
    expect(pageErrors).toEqual([]);
});

test("an error of a layout on autona.js shows in the check panel", async ({page}) => {
    await openCommentary(page, "commentators_e2e_broken");

    const panel = check(page);
    await expect(panel).toContainText("E2E broken (overlay contract 1)");
    await expect(panel).toContainText("Error in Autona.onUpdate");
    await expect(panel).toContainText("missingFunction is not defined");
    await expect(panel).toContainText("/layout/commentators_e2e_broken/index.js");
    await expect(panel.getByRole("button", {name: "Copy for an AI"}))
        .toBeVisible();
});

test("a design declaring the contract that does not load autona.js is told so", async ({page}) => {
    await openCommentary(page, "commentators_e2e_unloaded");

    await expect(check(page)).toContainText(
        "../include/autona.js is not loaded", {timeout: 15_000});
});

test("a layout on globals.js alone shows no check panel", async ({page}) => {
    await openCommentary(page, "commentators_e2e_legacy");
    // Past the time a layout on autona.js has to say it is ready.
    await page.waitForTimeout(6_000);

    await expect(check(page)).toHaveCount(0);
    await expect(page.getByRole("combobox", {name: "Preview data"}))
        .toHaveCount(0);
});

test("a layout that is fine says so, and the sample switch draws the sample", async ({page}) => {
    await openCommentary(page, "commentators_e2e_fine");
    await expect(page.getByRole("status", {name: CHECK}))
        .toContainText("no errors (overlay contract 1)");

    await page.getByRole("combobox", {name: "Preview data"}).click();
    await page.getByRole("option", {name: "Sample: 1on1 (singles)"}).click();

    const preview = page.getByTitle("Preview", {exact: true});
    await expect(preview).toHaveAttribute(
        "src", /\/layout\/commentators_e2e_fine\/index\.html\?autonaSample=singles$/);
    // The sample's casters shown (display order, the named ones only).
    await expect(page.frameLocator('iframe[title="Preview"]').locator("#names"))
        .toHaveText("Kilo,Mike,November");
    await expect(page.getByRole("status", {name: CHECK}))
        .toContainText("no errors");
});

test("the folders the catalog does not list are shown with why", async ({page, request}) => {
    const body = await (await request.get(
        serverUrl("/api/v1/layouts?kind=commentary"))).json();
    expect(body.unlisted).toEqual(expect.arrayContaining([
        {folder: "commentators_e2e_pageless", kind: "commentary",
         reason: "no_entry", detail: "index.html"},
        {folder: "e2e_loose", kind: null, reason: "no_manifest", detail: null},
    ]));
    // None of Autona's own folders.
    expect(body.unlisted.map((folder: {folder: string}) => folder.folder)
        .filter((name: string) => !name.includes("e2e"))).toEqual([]);

    await openCommentary(page, DEFAULT_DESIGN);
    const list = page.getByRole("region", {name: UNLISTED});
    await expect(list).toContainText("layout/commentators_e2e_pageless/");
    await expect(list).toContainText("layout/e2e_loose/");

    // The Board's: its kinds' and the unknown ones'.
    await page.goto(serverUrl("/web/board"));
    const boardList = page.getByRole("region", {name: UNLISTED});
    await expect(boardList).toContainText("layout/e2e_loose/");
    await expect(boardList).not.toContainText("commentators_e2e_pageless");
});

test("a design listed by its name despite a broken settings.json is warned", async ({page, request}) => {
    const body = await (await request.get(
        serverUrl("/api/v1/layouts?kind=commentary"))).json();
    const entry = body.data.find(
        (listed: {id: string}) => listed.id === "commentators_e2e_badjson");
    expect(entry.warnings).toEqual([expect.objectContaining(
        {reason: "bad_settings"})]);

    await openCommentary(page, "commentators_e2e_badjson");
    const panel = check(page);
    await expect(panel).toContainText(
        "settings.json: settings.json is not valid JSON");
    await expect(panel).toContainText("JSONDecodeError");
});
