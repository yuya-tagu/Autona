import {APIRequestContext, expect, Page} from "@playwright/test";

// What the specs share: where the headless app is, and the few API calls
// they make around the browser.

export function serverUrl(pathname: string): string {
    const base = process.env.AUTONA_E2E_BASE_URL;
    if (!base) {
        throw new Error(
            "AUTONA_E2E_BASE_URL is not set: the global setup "
            + "(e2e/global-setup.ts) did not start the headless app");
    }
    return new URL(pathname, base).toString();
}

// Only the headless app: the Web UI and the overlays also ask Google Fonts,
// which a test must not depend on (offline runs, slow CI networks).
export async function keepLocal(page: Page) {
    const base = new URL(serverUrl("/"));
    await page.route((url) => url.host !== base.host, (route) => route.abort());
}

export async function boardScores(request: APIRequestContext, board = 1) {
    const response = await request.get(serverUrl("/api/v1/state"));
    expect(response.ok()).toBe(true);
    const teams = (await response.json()).data?.score?.[board]?.team ?? {};
    return [teams["1"]?.score, teams["2"]?.score];
}

// Board 1 at 0-0, through the API as a Stream Deck or Companion would.
//
// The headless shell seeds a board's player slots but not its scores (the
// desktop's score spin boxes publish those), and the tests share one app,
// so each starts from a known score.
export async function resetBoard(request: APIRequestContext, board = 1) {
    const response = await request.post(
        serverUrl(`/api/v1/scoreboards/${board}/score:set`),
        {data: {team1: 0, team2: 0}});
    expect(response.ok()).toBe(true);
    await expect.poll(() => boardScores(request, board)).toEqual([0, 0]);
}
