import {
    cloneLayer,
    createBindingLayer,
    createEmptyProject,
    sanitizeSlug,
    validateProject,
} from "./model";

describe("scoreboard designer model", () => {
    test("creates and validates a versioned 1920x1080 project", () => {
        const project = createEmptyProject("大会用");
        project.layers.push(createBindingLayer("p1.name", 1));
        expect(validateProject(project)).toBe(project);
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

    test("keeps optional group and auto layout metadata in schema v1", () => {
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

        expect(validateProject(project)).toBe(project);
        expect(project.layers[0].groupId).toBe("group-main");
        expect(project.groups[0].name).toBe("Main");
        expect(project.autoLayouts[0].layerIds).toEqual([layer.id]);
    });
});
