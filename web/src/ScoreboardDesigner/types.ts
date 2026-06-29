export const PROJECT_SCHEMA_VERSION = 1 as const;

export const BINDINGS = [
    "p1.name",
    "p1.sponsorName",
    "p1.sponsorLogo",
    "p1.score",
    "p1.countryFlag",
    "p1.seed",
    "p1.twitter",
    "p1.state",
    "p2.name",
    "p2.sponsorName",
    "p2.sponsorLogo",
    "p2.score",
    "p2.countryFlag",
    "p2.seed",
    "p2.twitter",
    "p2.state",
    "tournament.logo",
    "tournament.name",
    "match.phase",
    "match.round",
    "games",
] as const;

export type BindingKey = typeof BINDINGS[number];
export type LayerType = "image" | "data-text" | "data-image" | "game-history";
export type AnimationPreset =
    | "none"
    | "fade"
    | "slide-left"
    | "slide-right"
    | "slide-up"
    | "slide-down"
    | "zoom";

export interface LayerAnimation {
    preset: AnimationPreset;
    durationMs: number;
    delayMs: number;
}

export interface BaseLayer {
    id: string;
    name: string;
    type: LayerType;
    groupId?: string;
    x: number;
    y: number;
    width: number;
    height: number;
    rotation: number;
    opacity: number;
    zIndex: number;
    visible: boolean;
    animation: LayerAnimation;
}

export interface ImageLayer extends BaseLayer {
    type: "image";
    asset: string;
    objectFit: "contain" | "cover" | "fill";
}

export interface TypographySettings {
    fontFamily: string;
    fontSize: number;
    fontWeight: number;
    color: string;
    align: "left" | "center" | "right";
    italic: boolean;
    letterSpacing: number;
}

export interface DataTextLayer extends BaseLayer {
    type: "data-text";
    binding: Exclude<BindingKey,
        "p1.sponsorLogo" | "p1.countryFlag" |
        "p2.sponsorLogo" | "p2.countryFlag" |
        "tournament.logo" | "games">;
    typography: TypographySettings;
}

export interface DataImageLayer extends BaseLayer {
    type: "data-image";
    binding:
        | "p1.sponsorLogo" | "p1.countryFlag"
        | "p2.sponsorLogo" | "p2.countryFlag"
        | "tournament.logo";
    objectFit: "contain" | "cover" | "fill";
}

export interface GameHistoryLayer extends BaseLayer {
    type: "game-history";
    binding: "games";
    gap: number;
    neutralColor: string;
    p1Color: string;
    p2Color: string;
    borderColor: string;
    borderWidth: number;
    radius: number;
}

export type DesignerLayer =
    | ImageLayer
    | DataTextLayer
    | DataImageLayer
    | GameHistoryLayer;

export interface DesignerGroup {
    id: string;
    name: string;
    collapsed?: boolean;
}

export interface AutoLayoutFrame {
    id: string;
    name: string;
    layerIds: string[];
    direction: "row" | "column";
    padding: number;
    gap: number;
    align: "start" | "center" | "end" | "stretch";
}

export interface ScoreboardProject {
    schemaVersion: typeof PROJECT_SCHEMA_VERSION;
    name: string;
    canvas: {
        width: 1920;
        height: 1080;
        backgroundColor: string;
    };
    layers: DesignerLayer[];
    groups?: DesignerGroup[];
    autoLayouts?: AutoLayoutFrame[];
}

export interface DesignerAsset {
    path: string;
    blob: Blob;
    previewUrl: string;
}

export type AssetMap = Record<string, DesignerAsset>;
