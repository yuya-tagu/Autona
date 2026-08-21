export const PROJECT_SCHEMA_VERSION = 3 as const;
export const PREVIOUS_PROJECT_SCHEMA_VERSION = 2 as const;
export const LEGACY_PROJECT_SCHEMA_VERSION = 1 as const;

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
export type LayerType =
    | "image"
    | "data-text"
    | "data-image"
    | "game-history";
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

export interface CubicBezier {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
}

export interface MotionKeyframe {
    id: string;
    timeMs: number;
    x: number;
    y: number;
    rotation: number;
    scaleX: number;
    scaleY: number;
}

export interface VisualKeyframe extends MotionKeyframe {
    /** Multiplier applied to the layer's base opacity. */
    opacity: number;
}

export interface LayerMotionAnimation {
    renderer: "webAnimations";
    durationMs: number;
    delayMs: number;
    easing: CubicBezier;
    loop: boolean;
    keyframes: MotionKeyframe[];
}

export interface LayerAnimationSet {
    intro: LayerMotionAnimation;
    update: LayerMotionAnimation;
}

export interface EventTimeline {
    durationMs: number;
    delayMs: number;
    easing: CubicBezier;
    visualKeyframes: VisualKeyframe[];
    /** Reserved for future renderer-specific actions; currently always empty. */
    rendererActions: [];
}

export interface ChangeTimeline extends EventTimeline {
    swapAtMs: number;
    watchBindings: BindingKey[];
    interruptPolicy: "coalesceLatest";
}

export interface LayerEvents {
    sceneEnter?: EventTimeline;
    dataChange?: ChangeTimeline;
}

export interface LayerSourceRef {
    sourceDocumentId: string;
    nodeId: string;
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
    /** Figma reflection state; omitted by older projects. */
    flipX?: boolean;
    flipY?: boolean;
    /** True when a static asset already includes its Figma transform. */
    assetTransformApplied?: boolean;
    opacity: number;
    zIndex: number;
    visible: boolean;
    events: LayerEvents;
    sourceRef?: LayerSourceRef;
    /** schemaVersion 2 compatibility while projects are migrated to v3. */
    animations?: LayerAnimationSet;
    /** Legacy compatibility for schema v1 projects and older inspector UI. */
    animation?: LayerAnimation;
}

export interface ImageLayer extends BaseLayer {
    type: "image";
    asset: string;
    objectFit: "contain" | "cover" | "fill";
    /** Optional score-driven SVG styling; ordinary image layers are unchanged. */
    gameHistoryStyle?: GameHistoryImageStyle;
}

export type GameHistoryImageMode = "slot" | "parts";
export type GameHistoryImagePartRole = "set" | "p1-set" | "p2-set" | "winner";
export type GameHistoryPlayerPartRole = "p1-set" | "p2-set";
export type GameHistorySetIndex = 0 | 1 | 2;

/** An explicit SVG lamp assignment shown in the Designer. */
export interface GameHistoryPartTarget {
    role: GameHistoryPlayerPartRole;
    setIndex: GameHistorySetIndex;
}

/** The layer currently assigned to one of the explicit SVG lamp targets. */
export interface GameHistoryLayerAssignment {
    target: GameHistoryPartTarget;
    layerId?: string;
    layerName?: string;
}

/** The six BO5 lamp targets displayed by the Designer. */
export const GAME_HISTORY_PART_TARGETS: GameHistoryPartTarget[] = [
    {role: "p1-set", setIndex: 0},
    {role: "p1-set", setIndex: 1},
    {role: "p2-set", setIndex: 0},
    {role: "p2-set", setIndex: 1},
    // BO5's third point uses the same visual position for either player.
    {role: "p1-set", setIndex: 2},
    {role: "p2-set", setIndex: 2},
];

export interface GameHistoryImagePart {
    /** Child-element path from the sanitized SVG root; [] means the whole SVG layer. */
    path: number[];
    role: GameHistoryImagePartRole;
    /** Zero-based set index within the selected role's player when applicable. */
    setIndex?: number;
}

export interface GameHistoryImageStyle {
    /** Zero-based game slot. */
    slotIndex: number;
    /** Omitted in legacy projects; omitted means the original slot mode. */
    mode?: GameHistoryImageMode;
    /** SVG elements that are driven independently by the match state. */
    parts?: GameHistoryImagePart[];
    p1Color: string;
    p2Color: string;
    neutralColor: string;
    strokeWidth: number;
    /** Saved SVG source used by the local OBS browser source. */
    svgSource?: string;
}

export interface TypographySettings {
    fontFamily: string;
    /** Original family captured from Figma for Google Fonts loading. */
    googleFontFamily?: string;
    fontSize: number;
    fontWeight: number;
    color: string;
    align: "left" | "center" | "right";
    italic: boolean;
    letterSpacing: number;
}

export interface DataTextLayer extends BaseLayer {
    type: "data-text";
    binding?: Exclude<BindingKey,
        "p1.sponsorLogo" | "p1.countryFlag" |
        "p2.sponsorLogo" | "p2.countryFlag" |
        "tournament.logo" | "games">;
    /** Used when the layer is not connected to live Autona data. */
    text?: string;
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

export interface GameHistorySlot {
    /** SVG asset exported from one Figma game-history element. */
    asset: string;
    /** Position and size relative to the Game-History layer bounds. */
    x: number;
    y: number;
    width: number;
    height: number;
    opacity?: number;
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
    /** Exactly five custom SVG slots when imported from Figma. */
    slots?: GameHistorySlot[];
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

export type ProjectAssetKind = "image";

export interface ProjectAsset {
    id: string;
    kind: ProjectAssetKind;
    path: string;
    mediaType: string;
    sha256?: string;
}

export interface SourceDocument {
    id: string;
    provider: "figma" | "photoshop" | "illustrator" | "affinity";
    documentId: string;
    displayName: string;
    fingerprint: string;
    importedAt: string;
    /** True when an import contains only the currently selected source layers. */
    partial?: boolean;
    snapshot?: Record<string, Record<string, unknown>>;
}

export interface ScoreboardProject {
    schemaVersion: typeof PROJECT_SCHEMA_VERSION;
    id: string;
    name: string;
    canvas: {
        width: 1920;
        height: 1080;
        backgroundColor: string;
    };
    assets: ProjectAsset[];
    sources?: SourceDocument[];
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
