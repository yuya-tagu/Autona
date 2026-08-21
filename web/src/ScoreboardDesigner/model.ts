import {
    AnimationPreset,
    BINDINGS,
    BindingKey,
    CubicBezier,
    ChangeTimeline,
    DataImageLayer,
    DataTextLayer,
    DesignerLayer,
    EventTimeline,
    GameHistoryImagePart,
    GameHistoryImageStyle,
    GameHistoryLayer,
    LayerAnimation,
    LayerAnimationSet,
    LayerMotionAnimation,
    LEGACY_PROJECT_SCHEMA_VERSION,
    MotionKeyframe,
    PREVIOUS_PROJECT_SCHEMA_VERSION,
    ProjectAsset,
    PROJECT_SCHEMA_VERSION,
    ScoreboardProject,
    VisualKeyframe,
} from "./types";

export const CANVAS_WIDTH = 1920;
export const CANVAS_HEIGHT = 1080;

const DEFAULT_ANIMATION = {
    preset: "fade" as AnimationPreset,
    durationMs: 450,
    delayMs: 0,
};

export const DEFAULT_EASING: CubicBezier = {
    x1: 0.2,
    y1: 0.8,
    x2: 0.2,
    y2: 1,
};

export const BINDING_LABELS: Record<BindingKey, string> = {
    "p1.name": "P1 選手名",
    "p1.sponsorName": "P1 所属タグ",
    "p1.sponsorLogo": "P1 所属ロゴ",
    "p1.score": "P1 スコア",
    "p1.countryFlag": "P1 国旗",
    "p1.seed": "P1 Seed",
    "p1.twitter": "P1 X",
    "p1.state": "P1 地域",
    "p2.name": "P2 選手名",
    "p2.sponsorName": "P2 所属タグ",
    "p2.sponsorLogo": "P2 所属ロゴ",
    "p2.score": "P2 スコア",
    "p2.countryFlag": "P2 国旗",
    "p2.seed": "P2 Seed",
    "p2.twitter": "P2 X",
    "p2.state": "P2 地域",
    "tournament.logo": "大会ロゴ",
    "tournament.name": "大会名",
    "match.phase": "フェーズ",
    "match.round": "ラウンド",
    games: "ゲーム取得表示",
};

const IMAGE_BINDINGS = new Set<BindingKey>([
    "p1.sponsorLogo",
    "p1.countryFlag",
    "p2.sponsorLogo",
    "p2.countryFlag",
    "tournament.logo",
]);

export function createEmptyProject(name = "新しいデザイン"): ScoreboardProject {
    return {
        schemaVersion: PROJECT_SCHEMA_VERSION,
        id: createId(),
        name,
        canvas: {
            width: CANVAS_WIDTH,
            height: CANVAS_HEIGHT,
            backgroundColor: "transparent",
        },
        assets: [],
        layers: [],
    };
}

export function createMotionKeyframe(
    timeMs: number,
    x: number,
    y: number,
    rotation: number,
    scaleX = 1,
    scaleY = 1,
): MotionKeyframe {
    return {
        id: createId(),
        timeMs,
        x,
        y,
        rotation,
        scaleX,
        scaleY,
    };
}

export function createVisualKeyframe(
    timeMs: number,
    x: number,
    y: number,
    rotation: number,
    scaleX = 1,
    scaleY = 1,
    opacity = 1,
): VisualKeyframe {
    return {
        ...createMotionKeyframe(timeMs, x, y, rotation, scaleX, scaleY),
        opacity,
    };
}

function presetStartFrame(
    animation: LayerAnimation,
    rotation: number,
): MotionKeyframe {
    const distance = 80;
    switch (animation.preset) {
        case "slide-left":
            return createMotionKeyframe(0, -distance, 0, rotation);
        case "slide-right":
            return createMotionKeyframe(0, distance, 0, rotation);
        case "slide-up":
            return createMotionKeyframe(0, 0, -distance, rotation);
        case "slide-down":
            return createMotionKeyframe(0, 0, distance, rotation);
        case "zoom":
            return createMotionKeyframe(0, 0, 0, rotation, 0.72, 0.72);
        default:
            return createMotionKeyframe(0, 0, 0, rotation);
    }
}

export function createDefaultMotionAnimation(
    rotation: number,
    legacy: LayerAnimation = DEFAULT_ANIMATION,
): LayerMotionAnimation {
    const durationMs = Math.max(0, legacy.durationMs);
    return {
        renderer: "webAnimations",
        durationMs,
        delayMs: Math.max(0, legacy.delayMs),
        easing: {...DEFAULT_EASING},
        loop: false,
        keyframes: legacy.preset === "none"
            ? [createMotionKeyframe(0, 0, 0, rotation)]
            : [
                presetStartFrame(legacy, rotation),
                createMotionKeyframe(durationMs, 0, 0, rotation),
            ],
    };
}

export function createDefaultAnimations(
    rotation: number,
    legacy: LayerAnimation = DEFAULT_ANIMATION,
): LayerAnimationSet {
    return {
        intro: createDefaultMotionAnimation(rotation, legacy),
        update: {
            renderer: "webAnimations",
            durationMs: 440,
            delayMs: 0,
            easing: {...DEFAULT_EASING},
            loop: false,
            keyframes: [
                createMotionKeyframe(0, 0, 0, rotation, 1, 1),
                createMotionKeyframe(220, 0, 0, rotation, 1.04, 1.04),
                createMotionKeyframe(440, 0, 0, rotation, 1, 1),
            ],
        },
    };
}

function timelineFromMotion(
    animation: LayerMotionAnimation,
    startOpacity = 1,
): EventTimeline {
    return {
        durationMs: animation.durationMs,
        delayMs: animation.delayMs,
        easing: {...animation.easing},
        visualKeyframes: animation.keyframes.map((frame, index) => ({
            ...frame,
            opacity: index === 0 && animation.keyframes.length > 1
                ? startOpacity
                : 1,
        })),
        rendererActions: [],
    };
}

export function createDefaultEvents(
    rotation: number,
    legacy: LayerAnimation = DEFAULT_ANIMATION,
): DesignerLayer["events"] {
    const animations = createDefaultAnimations(rotation, legacy);
    const introOpacity = legacy.preset === "none" ? 1 : 0;
    return {
        sceneEnter: timelineFromMotion(animations.intro, introOpacity),
        dataChange: {
            durationMs: 440,
            delayMs: 0,
            easing: {...DEFAULT_EASING},
            swapAtMs: 220,
            watchBindings: [],
            interruptPolicy: "coalesceLatest",
            visualKeyframes: [
                createVisualKeyframe(0, 0, 0, rotation, 1, 1, 1),
                createVisualKeyframe(220, 0, 0, rotation, 1.04, 1.04, 0),
                createVisualKeyframe(440, 0, 0, rotation, 1, 1, 1),
            ],
            rendererActions: [],
        },
    };
}

export function createBindingLayer(
    binding: BindingKey,
    zIndex: number,
): DesignerLayer {
    const events = createDefaultEvents(0);
    if (events.dataChange) events.dataChange.watchBindings = [binding];
    const base = {
        id: createId(),
        name: BINDING_LABELS[binding],
        x: binding.startsWith("p2.") ? 1320 : 200,
        y: binding === "tournament.name" ? 80 : 880,
        width: binding.endsWith(".score") ? 120 : 400,
        height: binding.endsWith(".score") ? 120 : 70,
        rotation: 0,
        opacity: 1,
        zIndex,
        visible: true,
        animation: {...DEFAULT_ANIMATION},
        animations: createDefaultAnimations(0),
        events,
    };

    if (binding === "games") {
        return {
            ...base,
            type: "game-history",
            binding,
            width: 340,
            height: 42,
            gap: 10,
            neutralColor: "#252524",
            p1Color: "#dc143c",
            p2Color: "#3d8be8",
            borderColor: "#f0eae0",
            borderWidth: 2,
            radius: 8,
        } satisfies GameHistoryLayer;
    }

    if (IMAGE_BINDINGS.has(binding)) {
        return {
            ...base,
            type: "data-image",
            binding: binding as DataImageLayer["binding"],
            width: 100,
            height: 70,
            objectFit: "contain",
        } satisfies DataImageLayer;
    }

    return {
        ...base,
        type: "data-text",
        binding: binding as DataTextLayer["binding"],
        typography: {
            fontFamily: "LINE Seed JP, Noto Sans JP, sans-serif",
            fontSize: binding.endsWith(".score") ? 72 : 40,
            fontWeight: 700,
            color: "#ffffff",
            align: binding.startsWith("p2.") ? "right" : "left",
            italic: false,
            letterSpacing: 0,
        },
    } satisfies DataTextLayer;
}

export function createId(): string {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
        return crypto.randomUUID();
    }
    return `layer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function cloneLayer(layer: DesignerLayer, zIndex: number): DesignerLayer {
    const cloned = JSON.parse(JSON.stringify(layer)) as DesignerLayer;
    return {
        ...cloned,
        id: createId(),
        name: `${layer.name} コピー`,
        x: layer.x + 24,
        y: layer.y + 24,
        zIndex,
    };
}

function migrateLayer(value: any, sourceVersion: number): DesignerLayer {
    const legacyBakedSvg = value.assetTransformApplied === undefined &&
        value.type === "image" &&
        typeof value.asset === "string" &&
        /\.svg$/i.test(value.asset) &&
        Boolean(value.sourceRef);
    const legacyAnimation = value.animation &&
        typeof value.animation === "object"
        ? value.animation as LayerAnimation
        : DEFAULT_ANIMATION;
    const animations = value.animations ?? createDefaultAnimations(
        Number(value.rotation) || 0,
        legacyAnimation,
    );
    const events = sourceVersion === PROJECT_SCHEMA_VERSION && value.events
        ? value.events
        : {
        sceneEnter: timelineFromMotion(
            animations.intro,
            legacyAnimation.preset === "none" ? 1 : 0,
        ),
        dataChange: {
            ...timelineFromMotion(animations.update, sourceVersion <= 2 ? 0 : 1),
            swapAtMs: sourceVersion <= 2 ? 0 : Math.round(animations.update.durationMs / 2),
            watchBindings: value.binding ? [value.binding] : [],
            interruptPolicy: "coalesceLatest",
        },
        };
    const layer = {
        ...value,
        flipX: legacyBakedSvg ? false : value.flipX,
        flipY: legacyBakedSvg ? false : value.flipY,
        animation: legacyAnimation,
        animations,
        events,
        assetTransformApplied: value.assetTransformApplied !== undefined
            ? value.assetTransformApplied
            : (value.type === "image" &&
                typeof value.asset === "string" &&
                /\.svg$/i.test(value.asset) &&
                Boolean(value.sourceRef)),
    } as DesignerLayer;
    return layer;
}

function migratedAssets(project: any, layers: DesignerLayer[]): ProjectAsset[] {
    if (Array.isArray(project.assets)) return project.assets;
    const assets: ProjectAsset[] = [];
    const seen = new Set<string>();
    layers.forEach((layer) => {
        const paths = layer.type === "image"
            ? [layer.asset]
            : layer.type === "game-history" && Array.isArray(layer.slots)
                ? layer.slots.map((slot) => slot.asset)
                : [];
        paths.forEach((assetPath) => {
            if (seen.has(assetPath)) return;
            seen.add(assetPath);
            assets.push({
                id: `legacy-image-${assets.length + 1}`,
                kind: "image",
                path: assetPath,
                mediaType: assetPath.toLowerCase().endsWith(".svg")
                    ? "image/svg+xml"
                    : "image/png",
            });
        });
    });
    return assets;
}

export function migrateProject(value: unknown): ScoreboardProject {
    if (!value || typeof value !== "object") {
        throw new Error("project.json がオブジェクトではありません。");
    }
    const project = value as any;
    if (
        project.schemaVersion !== PROJECT_SCHEMA_VERSION &&
        project.schemaVersion !== PREVIOUS_PROJECT_SCHEMA_VERSION &&
        project.schemaVersion !== LEGACY_PROJECT_SCHEMA_VERSION
    ) {
        throw new Error(`未対応のschemaVersionです: ${project.schemaVersion}`);
    }
    const sourceVersion = project.schemaVersion;
    const layers = Array.isArray(project.layers)
        ? project.layers.map((layer: any) => migrateLayer(layer, sourceVersion))
        : project.layers;
    return {
        ...project,
        schemaVersion: PROJECT_SCHEMA_VERSION,
        id: typeof project.id === "string" && project.id ? project.id : createId(),
        assets: migratedAssets(project, layers ?? []),
        layers,
    } as ScoreboardProject;
}

function validateBezier(value: unknown, label: string): void {
    const easing = value as CubicBezier;
    for (const key of ["x1", "y1", "x2", "y2"] as const) {
        if (!Number.isFinite(easing?.[key]) || easing[key] < 0 || easing[key] > 1) {
            throw new Error(`${label}.easing.${key} が範囲外です。`);
        }
    }
}

function validateMotionAnimation(value: unknown, label: string): void {
    const animation = value as LayerMotionAnimation;
    if (!animation ||
        animation.renderer !== "webAnimations" ||
        !Number.isFinite(animation.durationMs) ||
        !Number.isFinite(animation.delayMs) ||
        animation.durationMs < 0 ||
        animation.delayMs < 0 ||
        typeof animation.loop !== "boolean" ||
        !Array.isArray(animation.keyframes) ||
        animation.keyframes.length < 1) {
        throw new Error(`${label} が不正です。`);
    }
    validateBezier(animation.easing, label);
    let previousTime = -1;
    animation.keyframes.forEach((frame, index) => {
        for (const key of ["timeMs", "x", "y", "rotation", "scaleX", "scaleY"] as const) {
            if (!Number.isFinite(frame[key])) {
                throw new Error(`${label}.keyframes[${index}].${key} が数値ではありません。`);
            }
        }
        if (frame.timeMs < 0 || frame.timeMs > animation.durationMs) {
            throw new Error(`${label}.keyframes[${index}].timeMs が範囲外です。`);
        }
        if (frame.timeMs < previousTime) {
            throw new Error(`${label}.keyframes の順序が不正です。`);
        }
        if (frame.scaleX <= 0 || frame.scaleY <= 0) {
            throw new Error(`${label}.keyframes[${index}] のscaleが不正です。`);
        }
        previousTime = frame.timeMs;
    });
}

function validateEventTimeline(
    value: unknown,
    label: string,
    change: boolean,
): void {
    const timeline = value as EventTimeline | ChangeTimeline;
    if (!timeline ||
        !Number.isFinite(timeline.durationMs) ||
        !Number.isFinite(timeline.delayMs) ||
        timeline.durationMs < 0 ||
        timeline.delayMs < 0 ||
        !Array.isArray(timeline.visualKeyframes) ||
        timeline.visualKeyframes.length < 1 ||
        !Array.isArray(timeline.rendererActions) ||
        timeline.rendererActions.length > 0) {
        throw new Error(`${label} が不正です。`);
    }
    validateBezier(timeline.easing, label);
    let previousTime = -1;
    timeline.visualKeyframes.forEach((frame, index) => {
        for (const key of [
            "timeMs", "x", "y", "rotation", "scaleX", "scaleY", "opacity",
        ] as const) {
            if (!Number.isFinite(frame[key])) {
                throw new Error(`${label}.visualKeyframes[${index}].${key} が数値ではありません。`);
            }
        }
        if (frame.timeMs < 0 || frame.timeMs > timeline.durationMs ||
            frame.timeMs < previousTime) {
            throw new Error(`${label}.visualKeyframes[${index}].timeMs が不正です。`);
        }
        if (frame.scaleX <= 0 || frame.scaleY <= 0 ||
            frame.opacity < 0 || frame.opacity > 1) {
            throw new Error(`${label}.visualKeyframes[${index}] の表示値が不正です。`);
        }
        previousTime = frame.timeMs;
    });
    if (change) {
        const update = timeline as ChangeTimeline;
        if (!Number.isFinite(update.swapAtMs) ||
            update.swapAtMs < 0 || update.swapAtMs > update.durationMs ||
            update.interruptPolicy !== "coalesceLatest" ||
            !Array.isArray(update.watchBindings) ||
            update.watchBindings.some((binding) => !BINDINGS.includes(binding))) {
            throw new Error(`${label} の値変更設定が不正です。`);
        }
    }
}

export function createDefaultGameHistoryImageStyle(
    slotIndex = 0,
): GameHistoryImageStyle {
    return {
        slotIndex: Math.max(0, Math.min(4, Math.round(slotIndex))),
        p1Color: "#dc143c",
        p2Color: "#3d8be8",
        neutralColor: "#f0eae0",
        strokeWidth: 3,
    };
}

function validateGameHistoryImageParts(
    style: GameHistoryImageStyle,
    label: string,
): void {
    if (style.mode !== undefined && style.mode !== "slot" && style.mode !== "parts") {
        throw new Error(`${label}.modeが不正です。`);
    }
    if (style.parts === undefined) return;
    if (!Array.isArray(style.parts) || style.parts.length > 100) {
        throw new Error(`${label}.partsが不正です。`);
    }
    const paths = new Map<string, GameHistoryImagePart[]>();
    style.parts.forEach((part: GameHistoryImagePart, index) => {
        const pathKey = Array.isArray(part?.path) ? part.path.join(".") : "";
        const samePathParts = paths.get(pathKey) ?? [];
        const canSharePath = samePathParts.length > 0 &&
            samePathParts.every((existing) =>
                existing.role !== part?.role &&
                (existing.role === "p1-set" || existing.role === "p2-set") &&
                (part?.role === "p1-set" || part?.role === "p2-set") &&
                existing.setIndex === 2 && part?.setIndex === 2);
        if (!part || !Array.isArray(part.path) ||
            part.path.length > 64 ||
            part.path.some((value) => !Number.isInteger(value) || value < 0 || value > 10000) ||
            (samePathParts.length > 0 && !canSharePath) ||
            (part.role !== "set" && part.role !== "p1-set" &&
                part.role !== "p2-set" && part.role !== "winner")) {
            throw new Error(`${label}.parts[${index}]が不正です。`);
        }
        samePathParts.push(part);
        paths.set(pathKey, samePathParts);
        if ((part.role === "set" || part.role === "p1-set" || part.role === "p2-set") &&
            (!Number.isInteger(part.setIndex) || part.setIndex! < 0 || part.setIndex! > 4)) {
            throw new Error(`${label}.parts[${index}].setIndexが不正です。`);
        }
        if (part.role === "winner" && part.setIndex !== undefined) {
            throw new Error(`${label}.parts[${index}].setIndexが不正です。`);
        }
    });
}

function validateTypography(value: unknown, label: string): void {
    const typography = value as Record<string, unknown>;
    if (!typography || typeof typography.fontFamily !== "string" ||
        !typography.fontFamily.trim() ||
        (typography.googleFontFamily !== undefined &&
            (typeof typography.googleFontFamily !== "string" ||
                !typography.googleFontFamily.trim())) ||
        !Number.isFinite(typography.fontSize) ||
        !Number.isFinite(typography.fontWeight) ||
        !Number.isFinite(typography.letterSpacing) ||
        typeof typography.color !== "string" || !typography.color.trim() ||
        !["left", "center", "right"].includes(String(typography.align)) ||
        typeof typography.italic !== "boolean") {
        throw new Error(`${label} が不正です。`);
    }
    if (Number(typography.fontSize) < 1 || Number(typography.fontSize) > 1000 ||
        Number(typography.fontWeight) < 1 || Number(typography.fontWeight) > 1000) {
        throw new Error(`${label} のサイズまたは太さが範囲外です。`);
    }
}

export function sanitizeSlug(name: string): string {
    const normalized = name
        .normalize("NFKC")
        .toLowerCase()
        .trim()
        .replace(/[^\p{L}\p{N}_-]+/gu, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 48);
    return normalized || "custom";
}

export function validateProject(value: unknown): ScoreboardProject {
    const project = migrateProject(value);
    if (project.schemaVersion !== PROJECT_SCHEMA_VERSION) {
        throw new Error(`未対応のschemaVersionです: ${project.schemaVersion}`);
    }
    if (typeof project.name !== "string" || !project.name.trim()) {
        throw new Error("デザイン名がありません。");
    }
    if (project.canvas?.width !== CANVAS_WIDTH ||
        project.canvas?.height !== CANVAS_HEIGHT) {
        throw new Error("キャンバスは1920×1080である必要があります。");
    }
    if (!Array.isArray(project.layers)) {
        throw new Error("layers が配列ではありません。");
    }
    if (typeof project.id !== "string" || !project.id) {
        throw new Error("project.id がありません。");
    }
    if (!Array.isArray(project.assets)) {
        throw new Error("assets が配列ではありません。");
    }

    const ids = new Set<string>();
    const assetIds = new Set<string>();
    project.assets.forEach((asset, index) => {
        if (!asset || typeof asset.id !== "string" || !asset.id ||
            assetIds.has(asset.id) ||
            asset.kind !== "image" ||
            !/^assets\/[^/\\]+$/.test(asset.path) ||
            typeof asset.mediaType !== "string" || !asset.mediaType) {
            throw new Error(`assets[${index}] が不正です。`);
        }
        assetIds.add(asset.id);
    });
    const layerTypes = new Set([
        "image", "data-text", "data-image", "game-history",
    ]);
    const animationPresets = new Set([
        "none", "fade", "slide-left", "slide-right",
        "slide-up", "slide-down", "zoom",
    ]);
    project.layers.forEach((layer, index) => {
        if (!layer || typeof layer !== "object") {
            throw new Error(`layers[${index}] が不正です。`);
        }
        if (!layer.id || ids.has(layer.id)) {
            throw new Error(`layers[${index}] のidが空または重複しています。`);
        }
        if (!layerTypes.has(layer.type)) {
            throw new Error(`${layer.name || layer.id} のtypeが不正です。`);
        }
        ids.add(layer.id);
        for (const key of ["x", "y", "width", "height", "rotation", "opacity", "zIndex"] as const) {
            if (!Number.isFinite(layer[key])) {
                throw new Error(`${layer.name || layer.id}.${key} が数値ではありません。`);
            }
        }
        for (const key of ["flipX", "flipY"] as const) {
            if (layer[key] !== undefined && typeof layer[key] !== "boolean") {
                throw new Error(`${layer.name || layer.id}.${key} が不正です。`);
            }
        }
        if (layer.assetTransformApplied !== undefined &&
            typeof layer.assetTransformApplied !== "boolean") {
            throw new Error(`${layer.name || layer.id}.assetTransformApplied が不正です。`);
        }
        if (layer.width <= 0 || layer.height <= 0) {
            throw new Error(`${layer.name || layer.id} のサイズが不正です。`);
        }
        if (layer.opacity < 0 || layer.opacity > 1) {
            throw new Error(`${layer.name || layer.id} のopacityが範囲外です。`);
        }
        if (layer.animation && (
            !animationPresets.has(layer.animation.preset) ||
            !Number.isFinite(layer.animation.durationMs) ||
            !Number.isFinite(layer.animation.delayMs) ||
            layer.animation.durationMs < 0 ||
            layer.animation.delayMs < 0)) {
            throw new Error(`${layer.name || layer.id} のanimationが不正です。`);
        }
        if (layer.animations) {
            validateMotionAnimation(layer.animations.intro, `${layer.name || layer.id}.animations.intro`);
            validateMotionAnimation(layer.animations.update, `${layer.name || layer.id}.animations.update`);
        }
        if (!layer.events?.sceneEnter || !layer.events?.dataChange) {
            throw new Error(`${layer.name || layer.id} のeventsが不正です。`);
        }
        validateEventTimeline(
            layer.events.sceneEnter,
            `${layer.name || layer.id}.events.sceneEnter`,
            false,
        );
        validateEventTimeline(
            layer.events.dataChange,
            `${layer.name || layer.id}.events.dataChange`,
            true,
        );
        if (layer.type === "image" &&
            (!/^assets\/[^/]+\.(png|svg)$/i.test(layer.asset) ||
                layer.asset.includes(".."))) {
            throw new Error(`${layer.name || layer.id} のassetが不正です。`);
        }
        if (layer.type === "image" &&
            !project.assets.some((asset) =>
                asset.kind === "image" && asset.path === layer.asset)) {
            throw new Error(`${layer.name || layer.id} のasset manifestがありません。`);
        }
        if (layer.type === "image" && layer.gameHistoryStyle !== undefined) {
            const style = layer.gameHistoryStyle;
            if (!/\.svg$/i.test(layer.asset) || !style ||
                !Number.isInteger(style.slotIndex) ||
                style.slotIndex < 0 || style.slotIndex > 4 ||
                !Number.isFinite(style.strokeWidth) ||
                style.strokeWidth < 0 || style.strokeWidth > 100 ||
                typeof style.p1Color !== "string" || !style.p1Color.trim() ||
                typeof style.p2Color !== "string" || !style.p2Color.trim() ||
                typeof style.neutralColor !== "string" ||
                !style.neutralColor.trim()) {
                throw new Error(`${layer.name || layer.id} のSVG Game-History設定が不正です。`);
            }
            validateGameHistoryImageParts(style, `${layer.name || layer.id}.gameHistoryStyle`);
        }
        if (layer.type === "data-text") {
            validateTypography(layer.typography, `${layer.name || layer.id}.typography`);
            const binding = layer.binding as BindingKey | undefined;
            if (binding !== undefined && (
                !BINDINGS.includes(binding) ||
                IMAGE_BINDINGS.has(binding) ||
                binding === "games"
            )) {
                throw new Error(`${layer.name || layer.id} のtext bindingが不正です。`);
            }
            if (binding === undefined && typeof layer.text !== "string") {
                throw new Error(`${layer.name || layer.id} のtextが不正です。`);
            }
        }
        if (layer.type === "data-image" &&
            (!BINDINGS.includes(layer.binding) ||
                !IMAGE_BINDINGS.has(layer.binding))) {
            throw new Error(`${layer.name || layer.id} のimage bindingが不正です。`);
        }
        if (layer.type === "game-history") {
            const binding: unknown = (layer as any).binding;
            if (binding !== "games") {
                throw new Error(`${layer.name || layer.id} のgames bindingが不正です。`);
            }
            if (layer.slots !== undefined) {
                if (!Array.isArray(layer.slots) || layer.slots.length !== 5) {
                    throw new Error(`${layer.name || layer.id} のGame-History要素は5つ必要です。`);
                }
                layer.slots.forEach((slot, slotIndex) => {
                    if (!slot || typeof slot.asset !== "string" ||
                        !/^assets\/[^/\\]+\.(png|svg)$/i.test(slot.asset) ||
                        !project.assets.some((asset) =>
                            asset.kind === "image" && asset.path === slot.asset)) {
                        throw new Error(
                            `${layer.name || layer.id}.slots[${slotIndex}].assetが不正です。`,
                        );
                    }
                    for (const key of ["x", "y", "width", "height"] as const) {
                        if (!Number.isFinite(slot[key])) {
                            throw new Error(
                                `${layer.name || layer.id}.slots[${slotIndex}].${key}が数値ではありません。`,
                            );
                        }
                    }
                    if (slot.width <= 0 || slot.height <= 0 ||
                        (slot.opacity !== undefined &&
                            (!Number.isFinite(slot.opacity) ||
                                slot.opacity < 0 || slot.opacity > 1))) {
                        throw new Error(
                            `${layer.name || layer.id}.slots[${slotIndex}]の表示値が不正です。`,
                        );
                    }
                });
            }
        }
    });
    return project;
}
