import {resolveBinding, SAMPLE_STATE} from "./bindings";

describe("scoreboard binding resolver", () => {
    test("resolves player and match fields", () => {
        expect(resolveBinding("p1.name", SAMPLE_STATE)).toBe("PLAYER ONE");
        expect(resolveBinding("p2.score", SAMPLE_STATE)).toBe(1);
        expect(resolveBinding("match.phase", SAMPLE_STATE)).toBe("Top 8");
        expect(resolveBinding("match.round", SAMPLE_STATE)).toBe("Winners Finals");
    });

    test("returns empty strings for missing optional values", () => {
        expect(resolveBinding("p1.sponsorLogo", SAMPLE_STATE)).toBe("");
        expect(resolveBinding("p1.countryFlag", SAMPLE_STATE)).toBe("");
    });

    test("honors the sponsor visibility setting", () => {
        const state = JSON.parse(JSON.stringify(SAMPLE_STATE));
        state.display_options = {show_sponsor: false};
        expect(resolveBinding("p1.sponsorName", state)).toBe("");
    });

    test("adds a logo cache-buster from state", () => {
        const state = JSON.parse(JSON.stringify(SAMPLE_STATE));
        state.tournamentInfo.logoVersion = 123;
        expect(resolveBinding("tournament.logo", state)).toBe("../logo.png?v=123");
    });
});
