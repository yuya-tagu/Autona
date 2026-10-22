import fs from "fs";
import path from "path";
import {
    cloneLayer,
    createBindingLayer,
    createDefaultGameHistoryImageStyle,
    createEmptyProject,
    sanitizeSlug,
    validateProject,
} from "./model";

// Shared with tests/test_designer_runtime_contract.py, which reads the same
// files through layout/designer_runtime/runtime.js: the editor and the OBS
// runtime must read every supported schemaVersion into the same v3 project.
const CONTRACT_FIXTURES = path.resolve(
    __dirname, "../../../tests/fixtures/designer");

function readContractFixture(name) {
    return JSON.parse(
        fs.readFileSync(path.join(CONTRACT_FIXTURES, name), "utf8"));
}

// The only differences the contract tolerates (same rules as the Python
// side): ids generated during migration differ by construction, and a
// missing assetTransformApplied means false to every reader.
function normalizeMigrated(migrated, input, expected) {
    const project = JSON.parse(JSON.stringify(migrated));
    if (typeof input.id === "string" && input.id) {
        expect(project.id).toBe(input.id);
    } else {
        expect(typeof project.id).toBe("string");
        expect(project.id).not.toBe("");
        project.id = expected.id;
    }
    project.layers.forEach((layer) => {
        ["intro", "update"].forEach((track) => layer.animations[track]
            .keyframes.forEach((frame) => delete frame.id));
        ["sceneEnter", "dataChange"].forEach((track) => layer.events[track]
            .visualKeyframes.forEach((frame) => delete frame.id));
        if (layer.assetTransformApplied === undefined) {
            layer.assetTransformApplied = false;
        }
    });
    return project;
}

describe("scoreboard designer model", () => {
    test("creates and validates a versioned 1920x1080 project", () => {
        const project = createEmptyProject("大会用");
        project.layers.push(createBindingLayer("p1.name", 1));
        expect(validateProject(project)).toMatchObject({schemaVersion: 3, name: "大会用"});
        expect(project.id).toBeTruthy();
        expect(project.assets).toEqual([]);
        expect(project.canvas).toMatchObject({width: 1920, height: 1080});
    });

    test("rejects duplicate layer ids", () => {
        const project = createEmptyProject();
        const layer = createBindingLayer("p1.name", 1);
        project.layers = [layer, {...layer}];
        expect(() => validateProject(project)).toThrow(/重複/);
    });

    test("rejects unsafe image asset paths", () => {
        const project = createEmptyProject();
        const layer = {
            ...createBindingLayer("tournament.logo", 1),
            type: "image",
            asset: "../secret.png",
            objectFit: "contain",
        };
        project.layers = [layer];
        expect(() => validateProject(project)).toThrow(/asset/);
    });

    test("preserves SVG image assets when migrating a Figma import", () => {
        const project = createEmptyProject("SVG import");
        const layer = {
            ...createBindingLayer("tournament.logo", 1),
            type: "image",
            asset: "assets/logo.svg",
            objectFit: "contain",
            sourceRef: {sourceDocumentId: "figma-source", nodeId: "logo"},
            flipX: true,
        };
        project.schemaVersion = 1;
        delete project.assets;
        project.layers = [layer];

        const migrated = validateProject(project);

        expect(migrated.assets).toEqual([
            expect.objectContaining({
                path: "assets/logo.svg",
                mediaType: "image/svg+xml",
            }),
        ]);
        expect(migrated.layers[0].assetTransformApplied).toBe(true);
        expect(migrated.layers[0].flipX).toBe(false);
    });

    test("migrates five custom Game-History SVG slots", () => {
        const project = createEmptyProject("Game history import");
        const layer = createBindingLayer("games", 1);
        layer.slots = Array.from({length: 5}, (_, index) => ({
            asset: `assets/game-${index + 1}.svg`,
            x: index * 48,
            y: 0,
            width: 40,
            height: 40,
        }));
        project.schemaVersion = 1;
        delete project.assets;
        project.layers = [layer];

        const migrated = validateProject(project);

        expect(migrated.layers[0].slots).toHaveLength(5);
        expect(migrated.assets).toHaveLength(5);
        expect(migrated.assets[4]).toMatchObject({
            path: "assets/game-5.svg",
            mediaType: "image/svg+xml",
        });
    });

    test("accepts score-driven styling on imported SVG image layers", () => {
        const project = createEmptyProject("SVG game history");
        project.assets = [{
            id: "game-icon",
            kind: "image",
            path: "assets/game-icon.svg",
            mediaType: "image/svg+xml",
        }];
        project.layers = [{
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/game-icon.svg",
            objectFit: "contain",
            gameHistoryStyle: {
                ...createDefaultGameHistoryImageStyle(2),
                strokeWidth: 4,
            },
        }];

        expect(validateProject(project).layers[0]).toMatchObject({
            type: "image",
            gameHistoryStyle: {
                slotIndex: 2,
                strokeWidth: 4,
            },
        });
    });

    test("accepts additive SVG Game-History part settings without changing slot mode", () => {
        const project = createEmptyProject("SVG game history parts");
        project.assets = [{
            id: "game-icon",
            kind: "image",
            path: "assets/game-icon.svg",
            mediaType: "image/svg+xml",
        }];
        project.layers = [{
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/game-icon.svg",
            objectFit: "contain",
            gameHistoryStyle: {
                ...createDefaultGameHistoryImageStyle(),
                mode: "parts",
                parts: [
                    {path: [], role: "p1-set", setIndex: 0},
                    {path: [1], role: "p1-set", setIndex: 1},
                    {path: [2], role: "p2-set", setIndex: 0},
                    {path: [3], role: "p2-set", setIndex: 2},
                    {path: [4], role: "winner"},
                    {path: [5], role: "p1-set", setIndex: 2},
                    {path: [5], role: "p2-set", setIndex: 2},
                ],
            },
        }];

        expect(validateProject(project).layers[0]).toMatchObject({
            gameHistoryStyle: {
                mode: "parts",
                parts: [
                    {path: [], role: "p1-set", setIndex: 0},
                    {path: [1], role: "p1-set", setIndex: 1},
                    {path: [2], role: "p2-set", setIndex: 0},
                    {path: [3], role: "p2-set", setIndex: 2},
                    {path: [4], role: "winner"},
                    {path: [5], role: "p1-set", setIndex: 2},
                    {path: [5], role: "p2-set", setIndex: 2},
                ],
            },
        });
    });

    test("rejects duplicate SVG Game-History part paths", () => {
        const project = createEmptyProject("Invalid SVG game history parts");
        project.assets = [{
            id: "game-icon",
            kind: "image",
            path: "assets/game-icon.svg",
            mediaType: "image/svg+xml",
        }];
        project.layers = [{
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/game-icon.svg",
            objectFit: "contain",
            gameHistoryStyle: {
                ...createDefaultGameHistoryImageStyle(),
                mode: "parts",
                parts: [
                    {path: [0], role: "set", setIndex: 0},
                    {path: [0], role: "winner"},
                ],
            },
        }];

        expect(() => validateProject(project)).toThrow(/parts/);
    });

    test("uses a safe fallback for project folder slugs", () => {
        expect(sanitizeSlug("My Grand Finals")).toBe("my-grand-finals");
        expect(sanitizeSlug("決勝 デザイン")).toBe("決勝-デザイン");
        expect(sanitizeSlug("../../")).toBe("custom");
    });

    test("clones a layer with a new id and z-index", () => {
        const layer = createBindingLayer("p1.score", 2);
        const copy = cloneLayer(layer, 5);
        expect(copy.id).not.toBe(layer.id);
        expect(copy.zIndex).toBe(5);
        expect(copy.x).toBe(layer.x + 24);
    });

    test("keeps optional group and auto layout metadata in schema v3", () => {
        const project = createEmptyProject();
        const layer = createBindingLayer("p1.name", 1);
        layer.groupId = "group-main";
        project.layers = [layer];
        project.groups = [{id: "group-main", name: "Main", collapsed: false}];
        project.autoLayouts = [{
            id: "layout-main",
            name: "Main layout",
            layerIds: [layer.id],
            direction: "row",
            padding: 16,
            gap: 8,
            align: "center",
        }];

        expect(validateProject(project)).toMatchObject({schemaVersion: 3});
        expect(project.layers[0].groupId).toBe("group-main");
        expect(project.groups[0].name).toBe("Main");
        expect(project.autoLayouts[0].layerIds).toEqual([layer.id]);
    });

    test("migrates schema v1 animation presets to schema v3 event timelines", () => {
        const project = createEmptyProject();
        const layer = createBindingLayer("p1.name", 1);
        const legacyProject = {
            ...project,
            schemaVersion: 1,
            layers: [{
                ...layer,
                animations: undefined,
                animation: {preset: "slide-left", durationMs: 300, delayMs: 20},
            }],
        };

        const migrated = validateProject(legacyProject);

        expect(migrated.schemaVersion).toBe(3);
        expect(migrated.layers[0].animations.intro.durationMs).toBe(300);
        expect(migrated.layers[0].animations.intro.keyframes[0].x).toBe(-80);
        expect(migrated.layers[0].events.sceneEnter.durationMs).toBe(300);
        expect(migrated.layers[0].events.dataChange.swapAtMs).toBe(0);
    });

    test("does not rotate generated keyframes on top of a rotated legacy layer", () => {
        // The canvas and runtime.js rotate the layer box and animate its
        // content inside it, so a keyframe rotation equal to the layer's
        // would leave the layer at twice its angle once the intro ends.
        const layer = createBindingLayer("p1.name", 1);
        const migrated = validateProject({
            ...createEmptyProject(),
            schemaVersion: 1,
            layers: [{
                ...layer,
                rotation: 30,
                animations: undefined,
                events: undefined,
                animation: {preset: "slide-left", durationMs: 300, delayMs: 0},
            }],
        }).layers[0];

        const frames = [
            ...migrated.animations.intro.keyframes,
            ...migrated.animations.update.keyframes,
            ...migrated.events.sceneEnter.visualKeyframes,
            ...migrated.events.dataChange.visualKeyframes,
        ];
        expect(migrated.rotation).toBe(30);
        expect(frames.length).toBeGreaterThan(0);
        expect(frames.map((frame) => frame.rotation)).toEqual(frames.map(() => 0));
    });

    test("rejects invalid cubic bezier values", () => {
        const project = createEmptyProject();
        const layer = createBindingLayer("p1.name", 1);
        layer.animations.intro.easing.x1 = 1.2;
        project.layers = [layer];

        expect(() => validateProject(project)).toThrow(/easing/);
    });

    test("accepts a static data-text layer without a binding", () => {
        const project = createEmptyProject();
        const layer = createBindingLayer("p1.name", 1);
        delete layer.binding;
        layer.text = "Grand Finals";
        project.layers = [layer];

        expect(validateProject(project).layers[0]).toMatchObject({
            type: "data-text",
            text: "Grand Finals",
        });
    });

    test("migrates a Figma dynamic text layer without losing its font", () => {
        const project = createEmptyProject("Figma text");
        project.schemaVersion = 1;
        project.layers = [{
            id: "figma-text",
            name: "Player score",
            type: "data-text",
            binding: "p1.score",
            typography: {
                fontFamily: '"M PLUS 1p", sans-serif',
                googleFontFamily: "M PLUS 1p",
                fontSize: 64,
                fontWeight: 800,
                color: "#f7f2e8",
                align: "center",
                italic: false,
                letterSpacing: 1.5,
            },
            x: 700,
            y: 100,
            width: 120,
            height: 90,
            rotation: 0,
            opacity: 1,
            zIndex: 1,
            visible: true,
            animation: {preset: "fade", durationMs: 450, delayMs: 0},
        }];

        const migrated = validateProject(project);

        expect(migrated.layers[0]).toMatchObject({
            type: "data-text",
            binding: "p1.score",
            typography: project.layers[0].typography,
        });
        expect(migrated.layers[0].events.dataChange.watchBindings)
            .toEqual(["p1.score"]);
    });

    test("accepts Figma reflection flags on imported layers", () => {
        const project = createEmptyProject();
        const layer = createBindingLayer("p1.name", 1);
        layer.flipX = true;
        layer.flipY = false;
        project.layers = [layer];

        expect(validateProject(project).layers[0]).toMatchObject({
            flipX: true,
            flipY: false,
        });
    });
});

describe("migration contract shared with the OBS runtime", () => {
    const expected = readContractFixture("expected_v3.json");

    test.each(["v1.json", "v2.json", "v3.json"])(
        "%s reads as expected_v3.json",
        (name) => {
            const input = readContractFixture(name);
            const migrated = validateProject(input);
            expect(normalizeMigrated(migrated, input, expected)).toEqual(expected);
        },
    );

    // Inputs both must turn away with the same message (the Python side
    // runs them through runtime.js): a v1/v2 reference that is null or not
    // a string is skipped while deriving the manifest, then rejected.
    test.each(readContractFixture("invalid_v1.json")
        .map((entry) => [entry.label, entry]))(
        "rejects %s like the runtime",
        (_label, entry) => {
            let thrown;
            try {
                validateProject(entry.project);
            } catch (error) {
                thrown = error;
            }
            expect(thrown).toBeInstanceOf(Error);
            expect(thrown).not.toBeInstanceOf(TypeError);
            expect(thrown.message).toBe(entry.error);
        },
    );
});

// AutonaDesignerService only checks safety (sizes, asset paths, PNG/SVG
// content, known schemaVersion); everything below is judged here alone.
describe("project semantics owned by model.ts", () => {
    const layerById = (project, id) =>
        project.layers.find((layer) => layer.id === id);
    const slotsOf = (project) => layerById(project, "games").slots;
    const cases = [
        ["an unknown schemaVersion", (p) => { p.schemaVersion = 4; }, /schemaVersion/],
        ["a canvas other than 1920x1080", (p) => { p.canvas.width = 1280; }, /1920/],
        ["a duplicate asset id", (p) => { p.assets[1].id = p.assets[0].id; }, /assets\[1\]/],
        ["a non-image asset kind", (p) => { p.assets[0].kind = "font"; }, /assets\[0\]/],
        ["an unknown layer type", (p) => { layerById(p, "frame").type = "video"; }, /type/],
        ["a non-numeric position", (p) => { layerById(p, "frame").x = "10"; }, /数値/],
        ["a zero-sized layer", (p) => { layerById(p, "frame").width = 0; }, /サイズ/],
        ["opacity above 1", (p) => { layerById(p, "frame").opacity = 1.5; }, /opacity/],
        ["a non-boolean flip", (p) => { layerById(p, "logo").flipX = "yes"; }, /flipX/],
        ["a non-boolean assetTransformApplied",
            (p) => { layerById(p, "logo").assetTransformApplied = "true"; },
            /assetTransformApplied/],
        ["an unknown animation preset",
            (p) => { layerById(p, "frame").animation.preset = "spin"; }, /animation/],
        ["a motion keyframe after its duration",
            (p) => { layerById(p, "p1-name").animations.intro.keyframes[1].timeMs = 999; },
            /timeMs/],
        ["a non-Web-Animations renderer",
            (p) => { layerById(p, "p1-name").animations.update.renderer = "css"; },
            /animations\.update/],
        ["missing data-change events",
            (p) => { delete layerById(p, "p1-name").events.dataChange; }, /events/],
        ["renderer actions on scene enter",
            (p) => { layerById(p, "p1-name").events.sceneEnter.rendererActions = [{}]; },
            /sceneEnter/],
        ["a swap point after the timeline",
            (p) => { layerById(p, "p1-name").events.dataChange.swapAtMs = 9999; },
            /値変更/],
        ["an unknown watched binding",
            (p) => { layerById(p, "p1-name").events.dataChange.watchBindings = ["p3.name"]; },
            /値変更/],
        ["an image missing from the asset manifest",
            (p) => { p.assets = p.assets.filter((asset) => asset.path !== "assets/logo.svg"); },
            /manifest/],
        ["Game-History styling on a PNG",
            (p) => {
                layerById(p, "frame").gameHistoryStyle =
                    layerById(p, "set-icon").gameHistoryStyle;
            },
            /Game-History/],
        ["a Game-History slot index above 4",
            (p) => { layerById(p, "set-icon").gameHistoryStyle.slotIndex = 5; },
            /Game-History/],
        ["an image binding on a text layer",
            (p) => { layerById(p, "p1-name").binding = "p1.countryFlag"; }, /binding/],
        ["a static text layer without text",
            (p) => { delete layerById(p, "title").text; }, /text/],
        ["an unknown text alignment",
            (p) => { layerById(p, "title").typography.align = "justify"; }, /typography/],
        ["a zero font size",
            (p) => { layerById(p, "title").typography.fontSize = 0; }, /範囲外/],
        ["a text binding on an image layer",
            (p) => { layerById(p, "p1-flag").binding = "p1.name"; }, /binding/],
        ["a non-games binding on Game-History",
            (p) => { layerById(p, "games").binding = "p1.score"; }, /binding/],
        ["four Game-History slots", (p) => { slotsOf(p).pop(); }, /5/],
        ["a Game-History slot missing from the manifest",
            (p) => { slotsOf(p)[2].asset = "assets/other.png"; }, /slots\[2\]\.asset/],
        ["a zero-sized Game-History slot",
            (p) => { slotsOf(p)[0].height = 0; }, /slots\[0\]/],
    ];

    test("the contract fixture is valid as a whole", () => {
        expect(() => validateProject(readContractFixture("v3.json"))).not.toThrow();
    });

    test.each(cases)("rejects %s", (_label, mutate, message) => {
        const project = readContractFixture("v3.json");
        mutate(project);
        expect(() => validateProject(project)).toThrow(message);
    });
});
