import {createBindingLayer, createEmptyProject, validateProject} from "./model";
import {mergeImportedProject} from "./reimport";

function sourceProject(x, fingerprint) {
    const project = createEmptyProject("Imported");
    const layer = createBindingLayer("p1.name", 1);
    layer.id = "source-layer";
    layer.x = x;
    layer.sourceRef = {sourceDocumentId: "source-doc", nodeId: "name"};
    project.layers = [layer];
    project.sources = [{
        id: "source-doc",
        provider: "figma",
        documentId: "doc",
        displayName: "Scoreboard",
        fingerprint,
        importedAt: "2026-01-01T00:00:00Z",
        snapshot: {name: {x}},
    }];
    return project;
}

describe("source reimport merge", () => {
    test("updates unchanged source fields and preserves Autona animation", () => {
        const current = sourceProject(100, "old");
        const incoming = sourceProject(200, "new");
        const existingEvents = current.layers[0].events;

        const result = mergeImportedProject(current, incoming);

        expect(result.project.layers[0].x).toBe(200);
        expect(result.project.layers[0].events).toBe(existingEvents);
        expect(result.conflicts).toEqual([]);
    });

    test("updates an unchanged Figma horizontal flip", () => {
        const current = sourceProject(100, "old");
        current.layers[0].flipX = false;
        current.sources[0].snapshot.name.flipX = false;
        const incoming = sourceProject(100, "new");
        incoming.layers[0].flipX = true;
        incoming.sources[0].snapshot.name.flipX = true;

        const result = mergeImportedProject(current, incoming);

        expect(result.project.layers[0].flipX).toBe(true);
        expect(result.conflicts).toEqual([]);
    });

    test("keeps local edits when both source and Autona changed", () => {
        const current = sourceProject(150, "old");
        current.sources[0].snapshot.name.x = 100;
        const incoming = sourceProject(200, "new");

        const result = mergeImportedProject(current, incoming);

        expect(result.project.layers[0].x).toBe(150);
        expect(result.conflicts).toEqual([
            expect.objectContaining({nodeId: "name", field: "x"}),
        ]);
    });

    test("keeps source-deleted layers as orphans", () => {
        const current = sourceProject(100, "old");
        const incoming = sourceProject(100, "new");
        incoming.layers = [];

        const result = mergeImportedProject(current, incoming);

        expect(result.project.layers).toContain(current.layers[0]);
        expect(result.conflicts[0]).toMatchObject({
            nodeId: "name",
            kind: "removed-at-source",
        });
    });

    test("keeps a data binding assigned after import", () => {
        const current = sourceProject(100, "old");
        current.layers[0].binding = "match.round";
        current.layers[0].events.dataChange.watchBindings = ["match.round"];
        const incoming = sourceProject(100, "new");
        delete incoming.layers[0].binding;
        incoming.layers[0].text = "Grand Finals";

        const result = mergeImportedProject(current, incoming);

        expect(result.project.layers[0].binding).toBe("match.round");
        expect(result.project.layers[0].events.dataChange.watchBindings)
            .toEqual(["match.round"]);
    });

    test("keeps omitted layers when the incoming source is a partial Figma import", () => {
        const current = sourceProject(100, "old");
        const omitted = createBindingLayer("p1.score", 2);
        omitted.id = "source-score";
        omitted.sourceRef = {sourceDocumentId: "source-doc", nodeId: "score"};
        current.layers.push(omitted);
        current.sources[0].snapshot.score = {x: omitted.x};

        const incoming = sourceProject(200, "new");
        incoming.sources[0].partial = true;

        const result = mergeImportedProject(current, incoming);

        expect(result.project.layers).toHaveLength(2);
        expect(result.project.layers.find((layer) => layer.id === "source-layer").x)
            .toBe(200);
        expect(result.project.layers.find((layer) => layer.id === "source-score"))
            .toBeDefined();
        expect(result.conflicts).toEqual([]);
        expect(result.project.sources[0].snapshot.score).toEqual({x: omitted.x});
    });

    test("makes legacy asset IDs unique when a partial import is appended", () => {
        const current = sourceProject(100, "old");
        current.layers[0] = {
            ...current.layers[0],
            id: "source-first",
            type: "image",
            asset: "assets/first.png",
            objectFit: "contain",
            sourceRef: {sourceDocumentId: "source-doc", nodeId: "first"},
        };
        current.sources[0].snapshot.first = {x: 100};
        delete current.sources[0].snapshot.name;
        current.assets = [{
            id: "legacy-image-1",
            kind: "image",
            path: "assets/first.png",
            mediaType: "image/png",
        }];

        const incoming = sourceProject(200, "new");
        incoming.layers[0] = {
            ...incoming.layers[0],
            id: "source-second",
            type: "image",
            asset: "assets/second.png",
            objectFit: "contain",
            sourceRef: {sourceDocumentId: "source-doc", nodeId: "second"},
        };
        incoming.sources[0].partial = true;
        incoming.sources[0].snapshot.second = {x: 200};
        delete incoming.sources[0].snapshot.name;
        incoming.assets = [{
            id: "legacy-image-1",
            kind: "image",
            path: "assets/second.png",
            mediaType: "image/png",
        }];

        const result = mergeImportedProject(current, incoming);

        expect(result.project.assets.map((asset) => asset.id)).toEqual([
            "legacy-image-1",
            "legacy-image-1-2",
        ]);
        expect(() => validateProject(result.project)).not.toThrow();
    });
});
