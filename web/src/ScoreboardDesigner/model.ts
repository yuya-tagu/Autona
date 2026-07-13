import {
    AnimationPreset,
    BINDINGS,
    BindingKey,
    DataImageLayer,
    DataTextLayer,
    DesignerLayer,
    GameHistoryLayer,
    PROJECT_SCHEMA_VERSION,
    ScoreboardProject,
} from "./types";

export const CANVAS_WIDTH = 1920;
export const CANVAS_HEIGHT = 1080;

const DEFAULT_ANIMATION = {
    preset: "fade" as AnimationPreset,
    durationMs: 450,
    delayMs: 0,
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
        name,
        canvas: {
            width: CANVAS_WIDTH,
            height: CANVAS_HEIGHT,
            backgroundColor: "transparent",
        },
        layers: [],
    };
}

export function createBindingLayer(
    binding: BindingKey,
    zIndex: number,
): DesignerLayer {
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
    if (!value || typeof value !== "object") {
        throw new Error("project.json がオブジェクトではありません。");
    }
    const project = value as Partial<ScoreboardProject>;
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

    const ids = new Set<string>();
    const layerTypes = new Set(["image", "data-text", "data-image", "game-history"]);
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
        if (layer.width <= 0 || layer.height <= 0) {
            throw new Error(`${layer.name || layer.id} のサイズが不正です。`);
        }
        if (layer.opacity < 0 || layer.opacity > 1) {
            throw new Error(`${layer.name || layer.id} のopacityが範囲外です。`);
        }
        if (!layer.animation ||
            !animationPresets.has(layer.animation.preset) ||
            !Number.isFinite(layer.animation.durationMs) ||
            !Number.isFinite(layer.animation.delayMs) ||
            layer.animation.durationMs < 0 ||
            layer.animation.delayMs < 0) {
            throw new Error(`${layer.name || layer.id} のanimationが不正です。`);
        }
        if (layer.type === "image" &&
            (!/^assets\/[^/]+\.png$/i.test(layer.asset) || layer.asset.includes(".."))) {
            throw new Error(`${layer.name || layer.id} のassetが不正です。`);
        }
        if (layer.type === "data-text" || layer.type === "data-image" ||
            layer.type === "game-history") {
            const binding = layer.binding as BindingKey;
            if (!BINDINGS.includes(binding)) {
                throw new Error(`${layer.name || layer.id} のbindingが不正です。`);
            }
            if (layer.type === "data-text" &&
                (IMAGE_BINDINGS.has(binding) || binding === "games")) {
                throw new Error(`${layer.name || layer.id} のtext bindingが不正です。`);
            }
            if (layer.type === "data-image" &&
                !IMAGE_BINDINGS.has(binding)) {
                throw new Error(`${layer.name || layer.id} のimage bindingが不正です。`);
            }
            if (layer.type === "game-history" && binding !== "games") {
                throw new Error(`${layer.name || layer.id} のgames bindingが不正です。`);
            }
        }
    });
    return project as ScoreboardProject;
}
