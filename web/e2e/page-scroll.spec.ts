import {expect, test} from "@playwright/test";
import {keepLocal, serverUrl} from "./server";

// The desktop's shared view opens every React panel as /web/<panel>?desktop=1.
// A global `body { overflow: hidden }` (from the scoreboard page's App.css,
// which CRA bundles into every page) once made them unscrollable in a small
// window. Checked with the mouse wheel, as a user scrolls.

const PANELS = [
    "shell-check", "commentary", "stream", "event", "top8", "stats",
    "statsPreview", "playerBrowser", "thumbnail", "result", "board",
    "scoreboard",
];

test.beforeEach(async ({page}) => {
    await keepLocal(page);
});

for (const panel of PANELS) {
    test(`/web/${panel} scrolls the document in a narrow viewport`, async ({page}) => {
        await page.setViewportSize({width: 700, height: 120});
        await page.goto(serverUrl(`/web/${panel}?desktop=1&lng=en`));
        await expect(page.locator("#root > *").first()).toBeVisible();
        // Give the page time to render its content before measuring.
        await page.waitForTimeout(500);
        const tall = await page.evaluate(
            () => document.documentElement.scrollHeight > window.innerHeight);
        test.skip(!tall, "this panel fits the viewport");
        await page.mouse.move(350, 60);
        await page.mouse.wheel(0, 400);
        await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
    });
}

for (const route of ["/scoreboard", "/team"]) {
    test(`${route} (the overlay-style page) keeps its body overflow hidden`, async ({page}) => {
        await page.goto(serverUrl(route));
        await expect.poll(() => page.evaluate(
            () => getComputedStyle(document.body).overflow)).toBe("hidden");
    });
}

test("a shared-view page has the themed (thin, not Chromium's bright) scrollbar", async ({page}) => {
    await page.goto(serverUrl("/web/stream?desktop=1&lng=en"));
    await expect(page.locator("html")).toHaveClass(/panel-shell/);
    const style = await page.evaluate(() => {
        const cs = getComputedStyle(document.documentElement);
        return {color: cs.scrollbarColor, width: cs.scrollbarWidth};
    });
    expect(style.width).toBe("thin");
    expect(style.color).not.toBe("auto");
});

test("the overlay-style page keeps the default scrollbar", async ({page}) => {
    await page.goto(serverUrl("/scoreboard"));
    await expect.poll(() => page.evaluate(
        () => getComputedStyle(document.documentElement).scrollbarColor)).toBe("auto");
});
