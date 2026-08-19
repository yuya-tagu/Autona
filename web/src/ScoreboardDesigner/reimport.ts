import {
    DesignerLayer,
    ScoreboardProject,
    SourceDocument,
} from "./types";

export interface ReimportConflict {
    nodeId: string;
    layerName: string;
    field: string;
    kind: "changed-both" | "removed-at-source";
}

export interface ReimportResult {
    project: ScoreboardProject;
    conflicts: ReimportConflict[];
}

const SOURCE_FIELDS = [
    "name",
    "x",
    "y",
    "width",
    "height",
    "rotation",
    "flipX",
    "flipY",
    "opacity",
    "zIndex",
    "visible",
] as const;

function sourceKey(layer: DesignerLayer): string | null {
    return layer.sourceRef
        ? `${layer.sourceRef.sourceDocumentId}:${layer.sourceRef.nodeId}`
        : null;
}

function sameValue(left: unknown, right: unknown): boolean {
    return JSON.stringify(left) === JSON.stringify(right);
}

function uniqueAssetId(preferredId: string, usedIds: Set<string>): string {
    if (!usedIds.has(preferredId)) return preferredId;
    let suffix = 2;
    let candidate = `${preferredId}-${suffix}`;
    while (usedIds.has(candidate)) {
        suffix += 1;
        candidate = `${preferredId}-${suffix}`;
    }
    return candidate;
}

function mergeSourceLayer(
    current: DesignerLayer,
    incoming: DesignerLayer,
    previousSource: SourceDocument | undefined,
    conflicts: ReimportConflict[],
): DesignerLayer {
    const nodeId = incoming.sourceRef?.nodeId ?? incoming.id;
    const baseline = previousSource?.snapshot?.[nodeId] ?? {};
    const merged = {
        ...incoming,
        events: current.events,
        animations: current.animations,
        animation: current.animation,
    } as DesignerLayer;

    SOURCE_FIELDS.forEach((field) => {
        const oldValue = baseline[field];
        const currentValue = current[field];
        const incomingValue = incoming[field];
        if (oldValue === undefined || sameValue(currentValue, oldValue)) {
            (merged as any)[field] = incomingValue;
        } else if (sameValue(incomingValue, oldValue) ||
            sameValue(currentValue, incomingValue)) {
            (merged as any)[field] = currentValue;
        } else {
            (merged as any)[field] = currentValue;
            conflicts.push({
                nodeId,
                layerName: current.name,
                field,
                kind: "changed-both",
            });
        }
    });

    if (current.type === "data-text" && incoming.type === "data-text" &&
        merged.type === "data-text" &&
        current.binding) {
        merged.binding = current.binding;
        if (incoming.binding !== current.binding && current.events?.dataChange) {
            merged.events = {
                ...current.events,
                dataChange: {
                    ...current.events.dataChange,
                    watchBindings: [current.binding],
                },
            };
        }
    } else if ("binding" in current && "binding" in merged) {
        (merged as any).binding = current.binding;
    }
    if ("bindings" in current && "bindings" in merged) {
        (merged as any).bindings = current.bindings;
    }
    return merged;
}

export function mergeImportedProject(
    current: ScoreboardProject,
    incoming: ScoreboardProject,
): ReimportResult {
    const conflicts: ReimportConflict[] = [];
    const incomingSourceIds = new Set(
        (incoming.sources ?? []).map((source) => source.id),
    );
    const partialSourceIds = new Set(
        (incoming.sources ?? [])
            .filter((source) => source.partial)
            .map((source) => source.id),
    );
    const previousSources = new Map(
        (current.sources ?? []).map((source) => [source.id, source]),
    );
    const currentBySource = new Map(
        current.layers
            .map((layer) => [sourceKey(layer), layer] as const)
            .filter((entry): entry is [string, DesignerLayer] => Boolean(entry[0])),
    );
    const consumed = new Set<string>();
    const mergedIncoming = incoming.layers.map((layer) => {
        const key = sourceKey(layer);
        if (!key) return layer;
        consumed.add(key);
        const existing = currentBySource.get(key);
        if (!existing) return layer;
        return mergeSourceLayer(
            existing,
            layer,
            previousSources.get(layer.sourceRef!.sourceDocumentId),
            conflicts,
        );
    });

    current.layers.forEach((layer) => {
        const key = sourceKey(layer);
        if (!key || consumed.has(key) ||
            !incomingSourceIds.has(layer.sourceRef!.sourceDocumentId) ||
            partialSourceIds.has(layer.sourceRef!.sourceDocumentId)) return;
        mergedIncoming.push(layer);
        conflicts.push({
            nodeId: layer.sourceRef!.nodeId,
            layerName: layer.name,
            field: "layer",
            kind: "removed-at-source",
        });
    });

    const unaffectedLayers = current.layers.filter((layer) =>
        !layer.sourceRef ||
        !incomingSourceIds.has(layer.sourceRef.sourceDocumentId) ||
        (partialSourceIds.has(layer.sourceRef.sourceDocumentId) &&
            !consumed.has(sourceKey(layer)!)));
    const replacedAssetPaths = new Set<string>();
    current.layers.forEach((layer) => {
        const key = sourceKey(layer);
        if (!key || !consumed.has(key)) return;
        if (layer.type === "image") replacedAssetPaths.add(layer.asset);
    });
    const incomingAssetPaths = new Set(incoming.assets.map((asset) => asset.path));
    const assets = current.assets.filter((asset) =>
        !incomingAssetPaths.has(asset.path) &&
        !replacedAssetPaths.has(asset.path));
    const usedAssetIds = new Set(assets.map((asset) => asset.id));
    incoming.assets.forEach((asset) => {
        const id = uniqueAssetId(asset.id, usedAssetIds);
        usedAssetIds.add(id);
        assets.push(id === asset.id ? asset : {...asset, id});
    });
    const sourceById = new Map(
        (current.sources ?? []).map((source) => [source.id, source]),
    );
    (incoming.sources ?? []).forEach((source) => {
        const currentSource = sourceById.get(source.id);
        if (currentSource && source.partial) {
            sourceById.set(source.id, {
                ...currentSource,
                ...source,
                snapshot: {
                    ...(currentSource.snapshot ?? {}),
                    ...(source.snapshot ?? {}),
                },
            });
        } else {
            sourceById.set(source.id, source);
        }
    });
    const sources = [...sourceById.values()];

    return {
        project: {
            ...current,
            id: current.layers.length ? current.id : incoming.id,
            name: current.layers.length ? current.name : incoming.name,
            assets,
            sources,
            layers: [...unaffectedLayers, ...mergedIncoming],
        },
        conflicts,
    };
}
