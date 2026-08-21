import {SAMPLE_STATE} from "./bindings";
import {
    colorizeGameHistorySvg,
    colorizeGameHistorySvgParts,
    findSvgElementByPath,
    gameHistoryImagePartStatus,
    gameHistoryImagePartsStatus,
    isGameHistoryImagePartHidden,
    svgElementPath,
} from "./svgGameHistory";

const style = {
    slotIndex: 0,
    p1Color: "#dc143c",
    p2Color: "#3d8be8",
    neutralColor: "#f0eae0",
    strokeWidth: 3,
};

describe("SVG Game-History colorization", () => {
    test("uses an empty path for the complete SVG layer", () => {
        const root = document.createElementNS(
            "http://www.w3.org/2000/svg", "svg",
        );
        const path = document.createElementNS(
            "http://www.w3.org/2000/svg", "path",
        );
        root.appendChild(path);

        expect(svgElementPath(root, root)).toEqual([]);
        expect(svgElementPath(path, root)).toEqual([0]);
        expect(findSvgElementByPath(root, [])).toBe(root);
        expect(findSvgElementByPath(root, [0])).toBe(path);
    });

    test("limits styling to the imported SVG markup", () => {
        const uiSvg = document.createElementNS(
            "http://www.w3.org/2000/svg", "svg",
        );
        const uiPath = document.createElementNS(
            "http://www.w3.org/2000/svg", "path",
        );
        uiPath.setAttribute("fill", "currentColor");
        uiSvg.appendChild(uiPath);
        document.body.appendChild(uiSvg);

        const markup = colorizeGameHistorySvg(
            '<svg xmlns="http://www.w3.org/2000/svg"><path fill="blue" d="M0 0h10v10H0z"/></svg>',
            "p1",
            style,
        );

        expect(markup).toContain("#dc143c");
        expect(uiPath.getAttribute("fill")).toBe("currentColor");
        uiSvg.remove();
    });

    test("drives configured parts from both players' scores", () => {
        const parts = [
            {path: [0], role: "p1-set", setIndex: 0},
            {path: [1], role: "p1-set", setIndex: 1},
            {path: [2], role: "p2-set", setIndex: 0},
            {path: [3], role: "p2-set", setIndex: 1},
            {path: [4], role: "p2-set", setIndex: 2},
            {path: [5], role: "winner"},
        ];
        const partsStyle = {...style, mode: "parts", parts};

        expect(parts.map((part) => gameHistoryImagePartStatus(part, SAMPLE_STATE)))
            .toEqual(["p1", "p1", "p2", "neutral", "neutral", "neutral"]);
        expect(isGameHistoryImagePartHidden(
            {path: [4], role: "set", setIndex: 4}, SAMPLE_STATE,
        )).toBe(false);
        const unsetBestOf = JSON.parse(JSON.stringify(SAMPLE_STATE));
        unsetBestOf.score["1"].best_of = 0;
        expect(isGameHistoryImagePartHidden(
            {path: [0], role: "p1-set", setIndex: 1}, unsetBestOf,
        )).toBe(false);
        expect(isGameHistoryImagePartHidden(
            {path: [1], role: "p2-set", setIndex: 1}, unsetBestOf,
        )).toBe(false);

        const winnerState = JSON.parse(JSON.stringify(SAMPLE_STATE));
        winnerState.score["1"].team["1"].score = 3;
        expect(gameHistoryImagePartStatus(parts[5], winnerState)).toBe("p1");

        const markup = colorizeGameHistorySvgParts(
            '<svg xmlns="http://www.w3.org/2000/svg">'
                + '<path d="M0 0h1"/><path d="M1 0h1"/>'
                + '<path d="M2 0h1"/><path d="M3 0h1"/>'
                + '<path d="M4 0h1"/><path d="M5 0h1"/>'
                + '<path fill="lime" d="M6 0h1"/></svg>',
            partsStyle,
            SAMPLE_STATE,
        );

        expect(markup).toContain("#dc143c");
        expect(markup).toContain("#3d8be8");
        expect(markup).toContain('fill="lime"');
    });

    test("does not color a winning lamp until a player reaches the match target", () => {
        const winner = {path: [0], role: "winner"};
        const state = JSON.parse(JSON.stringify(SAMPLE_STATE));
        state.score["1"].team["1"].score = 2;
        state.score["1"].team["2"].score = 2;
        expect(gameHistoryImagePartStatus(winner, state)).toBe("neutral");
        state.score["1"].team["2"].score = 3;
        expect(gameHistoryImagePartStatus(winner, state)).toBe("p2");
    });

    test("lights a shared third-set path when either player reaches three", () => {
        const sharedParts = [
            {path: [0], role: "p1-set", setIndex: 2},
            {path: [0], role: "p2-set", setIndex: 2},
        ];
        const state = JSON.parse(JSON.stringify(SAMPLE_STATE));
        state.score["1"].team["1"].score = 3;
        state.score["1"].team["2"].score = 0;

        expect(gameHistoryImagePartsStatus(sharedParts, state)).toBe("p1");
        let markup = colorizeGameHistorySvgParts(
            '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1"/></svg>',
            {...style, mode: "parts", parts: sharedParts},
            state,
        );
        expect(markup).toContain("#dc143c");

        state.score["1"].team["1"].score = 0;
        state.score["1"].team["2"].score = 3;
        expect(gameHistoryImagePartsStatus(sharedParts, state)).toBe("p2");
        markup = colorizeGameHistorySvgParts(
            '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1"/></svg>',
            {...style, mode: "parts", parts: sharedParts},
            state,
        );
        expect(markup).toContain("#3d8be8");
    });
});
