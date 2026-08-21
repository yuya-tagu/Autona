import {
    cloneLayer,
    createBindingLayer,
    createDefaultGameHistoryImageStyle,
    createEmptyProject,
    sanitizeSlug,
    validateProject,
} from "./model";

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
