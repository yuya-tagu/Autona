import React from "react";
import {
    AppBar,
    Box,
    Button,
    ButtonGroup,
    Dialog,
    DialogContent,
    DialogTitle,
    Divider,
    IconButton,
    Menu,
    MenuItem,
    Select,
    Stack,
    Switch,
    TextField,
    Toolbar,
    Tooltip,
    Typography,
} from "@mui/material";
import {
    AddPhotoAlternate,
    ArrowDownward,
    ArrowUpward,
    ContentCopy,
    Delete,
    DragIndicator,
    Folder,
    FolderOpen,
    KeyboardArrowDown,
    KeyboardArrowRight,
    PlayArrow,
    Redo,
    Save,
    Undo,
    Visibility,
    VisibilityOff,
} from "@mui/icons-material";
import {useSelector} from "react-redux";
import {ReduxState} from "../redux/store";
import websocketInit from "../ScoreboardPage/websocketInit";
import socketConnection from "../websocketConnection";
import {resolveBinding, SAMPLE_STATE} from "./bindings";
import DesignerCanvas from "./DesignerCanvas";
import LayerInspector from "./LayerInspector";
import AnimationPanel from "./AnimationPanel";
import {
    AssetMap,
    BindingKey,
    DesignerGroup,
    DesignerLayer,
    GAME_HISTORY_PART_TARGETS,
    GameHistoryLayerAssignment,
    GameHistoryPartTarget,
    GameHistoryImagePartRole,
    ImageLayer,
    ScoreboardProject,
} from "./types";
import {
    BINDING_LABELS,
    cloneLayer,
    createBindingLayer,
    createDefaultAnimations,
    createDefaultEvents,
    createEmptyProject,
    createId,
} from "./model";
import {
    deleteDesktopProject,
    DesignerProjectSummary,
    isDesktopDesigner,
    listDesktopProjects,
    loadDesignerImport,
    loadDesktopProject,
    loadLatestFigmaImport,
    makeUniqueAssetPath,
    revokeAssetUrls,
    saveDesktopProject,
} from "./desktopApi";
import {mergeImportedProject} from "./reimport";
import {ensureProjectGoogleFonts} from "./fontLoader";
import {BINDINGS} from "./types";
import "./styles.css";

function maxZ(project: ScoreboardProject): number {
    return project.layers.reduce((max, layer) => Math.max(max, layer.zIndex), 0);
}

function isSharedGameHistorySet3(
    role: GameHistoryImagePartRole,
    setIndex?: number,
): boolean {
    return (role === "p1-set" || role === "p2-set") && setIndex === 2;
}

function orderedPanelLayers(project: ScoreboardProject): DesignerLayer[] {
    return [...project.layers].sort((a, b) => b.zIndex - a.zIndex);
}

async function readImageSize(file: File): Promise<{width: number; height: number}> {
    try {
        const bitmap = await createImageBitmap(file);
        const size = {width: bitmap.width, height: bitmap.height};
        bitmap.close();
        return size;
    } catch {
        const url = URL.createObjectURL(file);
        try {
            const image = await new Promise<HTMLImageElement>((resolve, reject) => {
                const element = new Image();
                element.onload = () => resolve(element);
                element.onerror = () => reject(new Error("画像を読み込めませんでした。"));
                element.src = url;
            });
            return {
                width: image.naturalWidth,
                height: image.naturalHeight,
            };
        } finally {
            URL.revokeObjectURL(url);
        }
    }
}

const AUTO_LAYER_NAME_BINDINGS = new Set<BindingKey>(["p1.name", "p2.name"]);

interface LayerDragState {
    sourceId: string;
    pointerY: number;
    offsetY: number;
    left: number;
    width: number;
    orderIds: string[];
}

interface LayerDragGeometry {
    listTop: number;
    rowHeight: number;
    rowStep: number;
}

interface PanelDragState {
    sourceKey: string;
    pointerY: number;
    offsetY: number;
    left: number;
    width: number;
    itemKeys: string[];
}

interface LayerPanelLayerItem {
    kind: "layer";
    layer: DesignerLayer;
}

interface LayerPanelGroupItem {
    kind: "group";
    group: DesignerGroup;
    layers: DesignerLayer[];
    collapsed: boolean;
}

type LayerPanelItem = LayerPanelLayerItem | LayerPanelGroupItem;
type EditorMode = "design" | "animate";
type AnimationTrackKey = "sceneEnter" | "dataChange";
type StandardTemplateKey =
    | "scoreboard"
    | "scoreboard_crewbattle"
    | "scoreboard_simple"
    | "scoreboard_team"
    | "scoreboard_topleft"
    | "scoreboard_Universal";

interface TemplateLayerSpec {
    binding: BindingKey;
    x: number;
    y: number;
    width: number;
    height: number;
    fontSize?: number;
    align?: "left" | "center" | "right";
}

const STANDARD_TEMPLATES: Array<{key: StandardTemplateKey; name: string}> = [
    {key: "scoreboard", name: "default_board"},
    {key: "scoreboard_crewbattle", name: "default_board_crewbattle"},
    {key: "scoreboard_simple", name: "default_board_simple"},
    {key: "scoreboard_team", name: "default_board_team"},
    {key: "scoreboard_topleft", name: "default_board_topleft"},
    {key: "scoreboard_Universal", name: "default_board_universal"},
];

function panelItemKey(item: LayerPanelItem): string {
    return item.kind === "group"
        ? `group:${item.group.id}`
        : `layer:${item.layer.id}`;
}

type ProjectUpdater = (project: ScoreboardProject) => ScoreboardProject;

interface ProjectTimeline {
    past: ScoreboardProject[];
    present: ScoreboardProject;
    future: ScoreboardProject[];
}

type ProjectTimelineAction =
    | {type: "apply"; updater: ProjectUpdater; recordHistory: boolean}
    | {type: "checkpoint"}
    | {type: "reset"; project: ScoreboardProject}
    | {type: "undo"}
    | {type: "redo"};

const MAX_HISTORY = 80;

function appendHistory(
    past: ScoreboardProject[],
    project: ScoreboardProject,
): ScoreboardProject[] {
    return [...past, project].slice(-MAX_HISTORY);
}

function projectTimelineReducer(
    state: ProjectTimeline,
    action: ProjectTimelineAction,
): ProjectTimeline {
    if (action.type === "apply") {
        const next = action.updater(state.present);
        if (next === state.present) return state;
        return {
            past: action.recordHistory
                ? appendHistory(state.past, state.present)
                : state.past,
            present: next,
            future: action.recordHistory ? [] : state.future,
        };
    }
    if (action.type === "checkpoint") {
        return {
            past: appendHistory(state.past, state.present),
            present: state.present,
            future: [],
        };
    }
    if (action.type === "reset") {
        return {past: [], present: action.project, future: []};
    }
    if (action.type === "undo") {
        const previous = state.past[state.past.length - 1];
        if (!previous) return state;
        return {
            past: state.past.slice(0, -1),
            present: previous,
            future: [state.present, ...state.future],
        };
    }
    if (action.type === "redo") {
        const next = state.future[0];
        if (!next) return state;
        return {
            past: appendHistory(state.past, state.present),
            present: next,
            future: state.future.slice(1),
        };
    }
    return state;
}

function resolvedAutoLayerName(layer: DesignerLayer, state: any): string | null {
    if (layer.type !== "data-text" || !layer.binding ||
        !AUTO_LAYER_NAME_BINDINGS.has(layer.binding)) {
        return null;
    }
    const value = String(resolveBinding(layer.binding, state)).trim();
    return value || null;
}

const TEMPLATE_LAYER_SPECS: Record<StandardTemplateKey, TemplateLayerSpec[]> = {
    scoreboard: [
        {binding: "p1.name", x: 380, y: 42, width: 500, height: 64, fontSize: 30, align: "left"},
        {binding: "p2.name", x: 1040, y: 42, width: 500, height: 64, fontSize: 30, align: "right"},
        {binding: "p1.score", x: 882, y: 42, width: 64, height: 64, fontSize: 42, align: "center"},
        {binding: "p2.score", x: 974, y: 42, width: 64, height: 64, fontSize: 42, align: "center"},
        {binding: "match.round", x: 760, y: 118, width: 400, height: 36, fontSize: 22, align: "center"},
        {binding: "match.phase", x: 760, y: 158, width: 400, height: 34, fontSize: 20, align: "center"},
        {binding: "games", x: 790, y: 190, width: 340, height: 32},
    ],
    scoreboard_crewbattle: [
        {binding: "tournament.name", x: 24, y: 45, width: 330, height: 28, fontSize: 16, align: "left"},
        {binding: "match.phase", x: 24, y: 76, width: 330, height: 32, fontSize: 18, align: "left"},
        {binding: "p1.name", x: 472, y: 51, width: 330, height: 52, fontSize: 26, align: "right"},
        {binding: "p2.name", x: 1118, y: 51, width: 330, height: 52, fontSize: 26, align: "left"},
        {binding: "p1.score", x: 826, y: 45, width: 90, height: 64, fontSize: 40, align: "center"},
        {binding: "p2.score", x: 1004, y: 45, width: 90, height: 64, fontSize: 40, align: "center"},
        {binding: "p1.seed", x: 24, y: 162, width: 288, height: 46, fontSize: 24, align: "left"},
        {binding: "p2.seed", x: 1608, y: 162, width: 288, height: 46, fontSize: 24, align: "right"},
    ],
    scoreboard_simple: [
        {binding: "tournament.name", x: 124, y: 10, width: 390, height: 38, fontSize: 22, align: "left"},
        {binding: "match.phase", x: 124, y: 50, width: 240, height: 30, fontSize: 18, align: "left"},
        {binding: "match.round", x: 124, y: 80, width: 240, height: 30, fontSize: 18, align: "left"},
        {binding: "p1.name", x: 495, y: 999, width: 222, height: 34, fontSize: 26, align: "left"},
        {binding: "p2.name", x: 1235, y: 999, width: 222, height: 34, fontSize: 26, align: "left"},
        {binding: "p1.score", x: 767, y: 998, width: 30, height: 37, fontSize: 30, align: "center"},
        {binding: "p2.score", x: 1507, y: 998, width: 30, height: 37, fontSize: 30, align: "center"},
        {binding: "p1.twitter", x: 613, y: 1039, width: 170, height: 20, fontSize: 16, align: "center"},
        {binding: "p2.twitter", x: 1353, y: 1039, width: 170, height: 20, fontSize: 16, align: "center"},
    ],
    scoreboard_team: [
        {binding: "p1.name", x: 444, y: 63, width: 380, height: 32, fontSize: 27, align: "left"},
        {binding: "p2.name", x: 1096, y: 63, width: 380, height: 32, fontSize: 27, align: "right"},
        {binding: "p1.sponsorName", x: 444, y: 96, width: 380, height: 20, fontSize: 16, align: "left"},
        {binding: "p2.sponsorName", x: 1096, y: 96, width: 380, height: 20, fontSize: 16, align: "right"},
        {binding: "p1.score", x: 829, y: 28, width: 82, height: 88, fontSize: 48, align: "center"},
        {binding: "p2.score", x: 1009, y: 28, width: 82, height: 88, fontSize: 48, align: "center"},
        {binding: "tournament.name", x: 16, y: 28, width: 340, height: 36, fontSize: 20, align: "left"},
        {binding: "match.phase", x: 16, y: 70, width: 340, height: 24, fontSize: 16, align: "left"},
        {binding: "match.round", x: 16, y: 94, width: 340, height: 24, fontSize: 16, align: "left"},
        {binding: "games", x: 790, y: 128, width: 340, height: 32},
    ],
    scoreboard_topleft: [
        {binding: "tournament.name", x: 32, y: 24, width: 220, height: 24, fontSize: 18, align: "left"},
        {binding: "match.phase", x: 32, y: 50, width: 160, height: 22, fontSize: 16, align: "left"},
        {binding: "match.round", x: 200, y: 50, width: 190, height: 22, fontSize: 16, align: "left"},
        {binding: "p1.name", x: 72, y: 76, width: 360, height: 42, fontSize: 24, align: "left"},
        {binding: "p2.name", x: 72, y: 120, width: 360, height: 42, fontSize: 24, align: "left"},
        {binding: "p1.score", x: 464, y: 76, width: 48, height: 42, fontSize: 30, align: "center"},
        {binding: "p2.score", x: 464, y: 120, width: 48, height: 42, fontSize: 30, align: "center"},
        {binding: "games", x: 88, y: 168, width: 300, height: 30},
    ],
    scoreboard_Universal: [
        {binding: "tournament.name", x: 560, y: 54, width: 800, height: 64, fontSize: 30, align: "center"},
        {binding: "p1.name", x: 170, y: 872, width: 520, height: 72, fontSize: 36, align: "left"},
        {binding: "p2.name", x: 1230, y: 872, width: 520, height: 72, fontSize: 36, align: "right"},
        {binding: "p1.score", x: 735, y: 842, width: 120, height: 110, fontSize: 56, align: "center"},
        {binding: "p2.score", x: 1065, y: 842, width: 120, height: 110, fontSize: 56, align: "center"},
        {binding: "p1.twitter", x: 200, y: 950, width: 260, height: 28, fontSize: 18, align: "left"},
        {binding: "p2.twitter", x: 1460, y: 950, width: 260, height: 28, fontSize: 18, align: "right"},
        {binding: "games", x: 790, y: 966, width: 340, height: 42},
    ],
};

function defaultTemplateProject(templateKey: StandardTemplateKey): ScoreboardProject {
    const template = STANDARD_TEMPLATES.find(({key}) => key === templateKey);
    const project = createEmptyProject(template?.name ?? `default_${templateKey}`);
    const specs = TEMPLATE_LAYER_SPECS[templateKey];
    project.layers = specs.map((spec, index) => {
        const layer = {
            ...createBindingLayer(spec.binding, index + 1),
            x: spec.x,
            y: spec.y,
            width: spec.width,
            height: spec.height,
            animations: createDefaultAnimations(0),
        } as DesignerLayer;
        if ("typography" in layer) {
            layer.typography = {
                ...layer.typography,
                ...(spec.fontSize ? {fontSize: spec.fontSize} : {}),
                ...(spec.align ? {align: spec.align} : {}),
            };
        }
        return layer;
    });
    return project;
}

function layerPanelItems(
    project: ScoreboardProject,
    orderedLayers: DesignerLayer[],
): LayerPanelItem[] {
    const groups = new Map((project.groups ?? []).map((group) => [group.id, group]));
    const groupedLayers = new Map<string, DesignerLayer[]>();
    orderedLayers.forEach((layer) => {
        if (!layer.groupId || !groups.has(layer.groupId)) return;
        groupedLayers.set(layer.groupId, [
            ...(groupedLayers.get(layer.groupId) ?? []),
            layer,
        ]);
    });

    const emittedGroups = new Set<string>();
    const items: LayerPanelItem[] = [];
    orderedLayers.forEach((layer) => {
        const group = layer.groupId ? groups.get(layer.groupId) : undefined;
        if (!group) {
            items.push({kind: "layer", layer});
            return;
        }
        if (emittedGroups.has(group.id)) return;
        emittedGroups.add(group.id);
        items.push({
            kind: "group",
            group,
            layers: groupedLayers.get(group.id) ?? [],
            collapsed: Boolean(group.collapsed),
        });
    });
    return items;
}

export default function ScoreboardDesigner() {
    const liveState = useSelector((state: ReduxState) => state.autonaState.autonaState);
    const websocketStatus = useSelector(
        (state: ReduxState) => state.websocketInfo.status,
    );
    const [projectTimeline, dispatchProject] = React.useReducer(
        projectTimelineReducer,
        undefined,
        () => ({
            past: [],
            present: createEmptyProject(),
            future: [],
        }),
    );
    const project = projectTimeline.present;
    const canUndo = projectTimeline.past.length > 0;
    const canRedo = projectTimeline.future.length > 0;
    const [assets, setAssets] = React.useState<AssetMap>({});
    const [selectedIds, setSelectedIds] = React.useState<string[]>([]);
    const [primarySelectedId, setPrimarySelectedId] = React.useState<string | null>(null);
    const [gameHistoryPartEditLayerId, setGameHistoryPartEditLayerId] =
        React.useState<string | null>(null);
    const [selectedGameHistoryPartPath, setSelectedGameHistoryPartPath] =
        React.useState<number[] | null>(null);
    const [bindingToAdd, setBindingToAdd] = React.useState<BindingKey>("p1.name");
    const [standardTemplate, setStandardTemplate] =
        React.useState<StandardTemplateKey>("scoreboard_Universal");
    const [sampleMode, setSampleMode] = React.useState(true);
    const [animationNonce, setAnimationNonce] = React.useState(0);
    const [editorMode, setEditorMode] = React.useState<EditorMode>("design");
    const [animationTrack, setAnimationTrack] =
        React.useState<AnimationTrackKey>("sceneEnter");
    const [message, setMessage] = React.useState("未保存");
    const [importAnchorEl, setImportAnchorEl] =
        React.useState<HTMLElement | null>(null);
    const [projectChoices, setProjectChoices] =
        React.useState<DesignerProjectSummary[]>([]);
    const [projectDialogOpen, setProjectDialogOpen] = React.useState(false);
    const [currentSlug, setCurrentSlug] = React.useState<string | null>(null);
    const [editingLayerId, setEditingLayerId] = React.useState<string | null>(null);
    const [editingLayerName, setEditingLayerName] = React.useState("");
    const [editingGroupId, setEditingGroupId] = React.useState<string | null>(null);
    const [editingGroupName, setEditingGroupName] = React.useState("");
    const [layerDrag, setLayerDrag] = React.useState<LayerDragState | null>(null);
    const [panelDrag, setPanelDrag] = React.useState<PanelDragState | null>(null);
    const imageInputRef = React.useRef<HTMLInputElement>(null);
    const svgInputRef = React.useRef<HTMLInputElement>(null);
    const handledImportIdsRef = React.useRef(new Set<string>());
    const layerListRef = React.useRef<HTMLDivElement>(null);
    const displayedLayersRef = React.useRef<DesignerLayer[]>([]);
    const assetsRef = React.useRef(assets);
    const autoLayerNamesRef = React.useRef<Record<string, string>>({});
    const canvasInteractionRef = React.useRef(false);
    const canvasInteractionCapturedRef = React.useRef(false);
    const layerRowRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});
    const panelRowRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});
    const previousLayerRowTopsRef = React.useRef<Map<string, number>>(new Map());
    const desktopMode = React.useMemo(isDesktopDesigner, []);
    const closeImportMenu = () => setImportAnchorEl(null);

    React.useEffect(() => {
        document.title = "Autona Scoreboard Designer";
        if (desktopMode) websocketInit();
    }, [desktopMode]);

    React.useEffect(() => {
        assetsRef.current = assets;
    }, [assets]);

    React.useEffect(() => {
        ensureProjectGoogleFonts(project);
    }, [project]);

    React.useEffect(
        () => () => revokeAssetUrls(assetsRef.current),
        [],
    );

    const orderedLayers = React.useMemo(() => orderedPanelLayers(project), [project]);
    const panelOrderedLayers = React.useMemo(() => {
        if (!layerDrag) return orderedLayers;
        const byId = new Map(orderedLayers.map((layer) => [layer.id, layer]));
        return layerDrag.orderIds
            .map((id) => byId.get(id))
            .filter(Boolean) as DesignerLayer[];
    }, [layerDrag, orderedLayers]);
    const basePanelItems = React.useMemo(
        () => layerPanelItems(project, panelOrderedLayers),
        [panelOrderedLayers, project],
    );
    const panelItems = React.useMemo(() => {
        if (!panelDrag) return basePanelItems;
        const byKey = new Map(basePanelItems.map((item) => [panelItemKey(item), item]));
        return panelDrag.itemKeys
            .map((key) => byKey.get(key))
            .filter(Boolean) as LayerPanelItem[];
    }, [basePanelItems, panelDrag]);
    const displayedLayers = React.useMemo(
        () => panelItems.flatMap((item) => {
            if (item.kind === "layer") return [item.layer];
            return item.collapsed ? [] : item.layers;
        }),
        [panelItems],
    );
    const displayedLayerOrderKey = React.useMemo(
        () => panelItems.map((item) =>
            item.kind === "layer"
                ? `layer:${item.layer.id}`
                : `group:${item.group.id}:${item.collapsed ? "closed" : "open"}:${item.layers.map((layer) => layer.id).join(",")}`,
        ).join("|"),
        [panelItems],
    );
    displayedLayersRef.current = displayedLayers;
    const floatingLayer = layerDrag
        ? orderedLayers.find((layer) => layer.id === layerDrag.sourceId) ?? null
        : null;
    const selectedLayer = project.layers.find((layer) => layer.id === primarySelectedId) ?? null;
    const gameHistoryAssignments = React.useMemo<GameHistoryLayerAssignment[]>(
        () => GAME_HISTORY_PART_TARGETS.map((target) => {
            const assignedLayer = project.layers.find((layer) =>
                layer.type === "image" &&
                layer.gameHistoryStyle?.parts?.some((part) =>
                    part.path.length === 0 &&
                    part.role === target.role &&
                    part.setIndex === target.setIndex));
            return {
                target,
                layerId: assignedLayer?.id,
                layerName: assignedLayer?.name,
            };
        }),
        [project.layers],
    );
    const selectedLayers = selectedIds
        .map((id) => project.layers.find((layer) => layer.id === id))
        .filter(Boolean) as DesignerLayer[];
    const selectedGroupId = React.useMemo(() => {
        const groupIds = new Set(
            selectedLayers
                .map((layer) => layer.groupId)
                .filter(Boolean) as string[],
        );
        if (groupIds.size !== 1) return null;
        const [groupId] = Array.from(groupIds);
        const groupLayerIds = project.layers
            .filter((layer) => layer.groupId === groupId)
            .map((layer) => layer.id);
        return groupLayerIds.length > 0 &&
            groupLayerIds.every((id) => selectedIds.includes(id))
            ? groupId
            : null;
    }, [project.layers, selectedIds, selectedLayers]);
    const bindingCounts = React.useMemo(() => {
        const counts = new Map<BindingKey, number>();
        project.layers.forEach((layer) => {
            if ("binding" in layer && layer.binding) {
                counts.set(layer.binding, (counts.get(layer.binding) ?? 0) + 1);
            }
        });
        return counts;
    }, [project.layers]);
    const hasLiveState = Boolean(liveState?.score?.[1]);
    const previewState = !sampleMode && hasLiveState
        ? liveState
        : SAMPLE_STATE;

    const applyProjectChange = React.useCallback((
        updater: ProjectUpdater,
        nextMessage = "変更あり",
        recordHistory = true,
    ) => {
        dispatchProject({type: "apply", updater, recordHistory});
        setMessage(nextMessage);
    }, []);

    const resetProject = React.useCallback((
        nextProject: ScoreboardProject,
        nextMessage: string,
    ) => {
        dispatchProject({type: "reset", project: nextProject});
        setMessage(nextMessage);
    }, []);

    const undoProject = React.useCallback(() => {
        if (!canUndo) return;
        dispatchProject({type: "undo"});
        setLayerDrag(null);
        setPanelDrag(null);
        setEditingLayerId(null);
        setEditingLayerName("");
        setEditingGroupId(null);
        setEditingGroupName("");
        setMessage("元に戻しました。");
    }, [canUndo]);

    const redoProject = React.useCallback(() => {
        if (!canRedo) return;
        dispatchProject({type: "redo"});
        setLayerDrag(null);
        setPanelDrag(null);
        setEditingLayerId(null);
        setEditingLayerName("");
        setEditingGroupId(null);
        setEditingGroupName("");
        setMessage("やり直しました。");
    }, [canRedo]);

    const beginCanvasInteraction = React.useCallback(() => {
        canvasInteractionRef.current = true;
        canvasInteractionCapturedRef.current = false;
    }, []);

    const endCanvasInteraction = React.useCallback(() => {
        canvasInteractionRef.current = false;
        canvasInteractionCapturedRef.current = false;
    }, []);

    React.useEffect(() => {
        if (sampleMode || !hasLiveState) return;
        dispatchProject({type: "apply", recordHistory: false, updater: (current) => {
            let changed = false;
            const nextAutoNames = {...autoLayerNamesRef.current};
            const layers = current.layers.map((layer) => {
                const autoName = resolvedAutoLayerName(layer, liveState);
                if (!autoName || !("binding" in layer)) {
                    delete nextAutoNames[layer.id];
                    return layer;
                }
                const previousAutoName = autoLayerNamesRef.current[layer.id];
                const defaultName = BINDING_LABELS[layer.binding as BindingKey];
                const canUpdate =
                    layer.name === defaultName ||
                    Boolean(previousAutoName && layer.name === previousAutoName);
                if (!canUpdate) return layer;
                nextAutoNames[layer.id] = autoName;
                if (layer.name === autoName) return layer;
                changed = true;
                return {...layer, name: autoName} as DesignerLayer;
            });
            autoLayerNamesRef.current = nextAutoNames;
            return changed ? {...current, layers} : current;
        }});
    }, [hasLiveState, liveState, project.layers, sampleMode]);

    React.useEffect(() => {
        const targetIds = new Set(project.layers.map((layer) => layer.id));
        const nextSelectedIds = selectedIds.filter((id) => targetIds.has(id));
        const nextPrimaryId = primarySelectedId && targetIds.has(primarySelectedId)
            ? primarySelectedId
            : nextSelectedIds[0] ?? null;
        if (
            nextSelectedIds.length !== selectedIds.length ||
            nextPrimaryId !== primarySelectedId
        ) {
            setSelectedIds(nextSelectedIds);
            setPrimarySelectedId(nextPrimaryId);
        }
    }, [primarySelectedId, project.layers, selectedIds]);

    React.useEffect(() => {
        const selected = project.layers.find((item) => item.id === primarySelectedId);
        if (!selected || selected.type !== "image" || !selected.gameHistoryStyle) {
            setGameHistoryPartEditLayerId(null);
            setSelectedGameHistoryPartPath(null);
        } else if (gameHistoryPartEditLayerId &&
            gameHistoryPartEditLayerId !== selected.id) {
            setGameHistoryPartEditLayerId(null);
            setSelectedGameHistoryPartPath(null);
        }
    }, [gameHistoryPartEditLayerId, primarySelectedId, project.layers]);

    React.useEffect(() => {
        const keyDown = (event: KeyboardEvent) => {
            const target = event.target as HTMLElement | null;
            if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
            if (!(event.ctrlKey || event.metaKey)) return;
            const key = event.key.toLowerCase();
            if (key === "z" && !event.shiftKey) {
                event.preventDefault();
                undoProject();
            } else if (key === "y" || (key === "z" && event.shiftKey)) {
                event.preventDefault();
                redoProject();
            }
        };
        window.addEventListener("keydown", keyDown);
        return () => window.removeEventListener("keydown", keyDown);
    }, [redoProject, undoProject]);

    React.useLayoutEffect(() => {
        const previous = previousLayerRowTopsRef.current;
        const next = new Map<string, number>();
        displayedLayersRef.current.forEach((layer) => {
            const element = layerRowRefs.current[layer.id];
            if (!element) return;
            const top = element.offsetTop;
            next.set(layer.id, top);
            const previousTop = previous.get(layer.id);
            if (
                previousTop === undefined ||
                previousTop === top ||
                layerDrag?.sourceId === layer.id
            ) {
                return;
            }
            const delta = previousTop - top;
            element.style.transition = "none";
            element.style.transform = `translateY(${delta}px)`;
            window.requestAnimationFrame(() => {
                element.style.transition = "";
                element.style.transform = "";
            });
        });
        previousLayerRowTopsRef.current = next;
    }, [displayedLayerOrderKey, layerDrag?.sourceId]);

    const setSelection = React.useCallback((ids: string[], primaryId: string | null) => {
        let nextIds = ids;
        if (ids.length === 1) {
            const layer = project.layers.find((item) => item.id === ids[0]);
            if (layer?.groupId) {
                nextIds = project.layers
                    .filter((item) => item.groupId === layer.groupId)
                    .map((item) => item.id);
            }
        }
        setSelectedIds(nextIds);
        setPrimarySelectedId(primaryId);
    }, [project.layers]);

    const selectGameHistoryAssignment = React.useCallback((layerId: string) => {
        setSelection([layerId], layerId);
        setGameHistoryPartEditLayerId(null);
        setSelectedGameHistoryPartPath(null);
    }, [setSelection]);

    const updateLayer = React.useCallback((
        id: string,
        patch: Partial<DesignerLayer>,
    ) => {
        if (canvasInteractionRef.current && !canvasInteractionCapturedRef.current) {
            dispatchProject({type: "checkpoint"});
            canvasInteractionCapturedRef.current = true;
        }
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.map((layer) =>
                layer.id === id
                    ? ({...layer, ...patch} as DesignerLayer)
                    : layer,
            ),
        }), "変更あり", !canvasInteractionRef.current);
    }, [applyProjectChange]);

    const updateGameHistoryPartEditing = React.useCallback((enabled: boolean) => {
        if (selectedLayer?.type !== "image" || !selectedLayer.gameHistoryStyle) return;
        updateLayer(selectedLayer.id, {
            gameHistoryStyle: {
                ...selectedLayer.gameHistoryStyle,
                mode: enabled ? "parts" : "slot",
                parts: selectedLayer.gameHistoryStyle.parts ?? [],
            },
        });
        setGameHistoryPartEditLayerId(enabled ? selectedLayer.id : null);
        setSelectedGameHistoryPartPath(null);
    }, [selectedLayer, updateLayer]);

    const updateGameHistoryPart = React.useCallback((
        layerId: string,
        path: number[],
        role: GameHistoryImagePartRole,
        setIndex?: number,
    ) => {
        const layer = project.layers.find((item) => item.id === layerId);
        if (layer?.type !== "image" || !layer.gameHistoryStyle) return;
        const pathKey = path.join(".");
        const hasSetIndex = role === "set" || role === "p1-set" || role === "p2-set";
        const maxSetIndex = role === "p1-set" || role === "p2-set" ? 2 : 4;
        const normalizedSetIndex = hasSetIndex
            ? Math.max(0, Math.min(maxSetIndex, Math.round(setIndex ?? 0)))
            : undefined;
        const parts = (layer.gameHistoryStyle.parts ?? []).filter((part) =>
            !(part.role === role && part.setIndex === normalizedSetIndex) &&
            (part.path.join(".") !== pathKey ||
                (isSharedGameHistorySet3(role, normalizedSetIndex) &&
                    isSharedGameHistorySet3(part.role, part.setIndex) &&
                    part.role !== role)));
        const nextPart = {
            path: [...path],
            role,
            ...(role === "set" || role === "p1-set" || role === "p2-set"
                ? {setIndex: normalizedSetIndex}
                : {}),
        };
        parts.push(nextPart);
        updateLayer(layerId, {
            gameHistoryStyle: {
                ...layer.gameHistoryStyle,
                mode: "parts",
                parts,
            },
        });
    }, [project.layers, updateLayer]);

    const selectGameHistoryPart = React.useCallback((layerId: string, path: number[]) => {
        setSelection([layerId], layerId);
        setGameHistoryPartEditLayerId(layerId);
        setSelectedGameHistoryPartPath(path);
    }, [setSelection]);

    const assignSelectedGameHistoryPart = React.useCallback((
        target: GameHistoryPartTarget,
    ) => {
        if (selectedLayer?.type !== "image" || !selectedLayer.gameHistoryStyle) return;
        // An empty path represents the selected SVG image layer itself. This
        // lets the inspector assign a complete SVG layer without a canvas
        // click on one of its child elements.
        updateGameHistoryPart(
            selectedLayer.id,
            [],
            target.role,
            target.setIndex,
        );
        setGameHistoryPartEditLayerId(null);
        setSelectedGameHistoryPartPath(null);
    }, [selectedLayer, updateGameHistoryPart]);

    const updateSelectedGameHistoryPart = React.useCallback((
        role: GameHistoryImagePartRole,
        setIndex?: number,
    ) => {
        if (selectedLayer?.type !== "image" || selectedGameHistoryPartPath === null) return;
        updateGameHistoryPart(
            selectedLayer.id,
            selectedGameHistoryPartPath,
            role,
            setIndex,
        );
    }, [selectedGameHistoryPartPath, selectedLayer, updateGameHistoryPart]);

    const removeSelectedGameHistoryPart = React.useCallback(() => {
        if (selectedLayer?.type !== "image" || !selectedLayer.gameHistoryStyle ||
            selectedGameHistoryPartPath === null) return;
        const pathKey = selectedGameHistoryPartPath.join(".");
        updateLayer(selectedLayer.id, {
            gameHistoryStyle: {
                ...selectedLayer.gameHistoryStyle,
                parts: (selectedLayer.gameHistoryStyle.parts ?? [])
                    .filter((part) => part.path.join(".") !== pathKey),
            },
        });
    }, [selectedGameHistoryPartPath, selectedLayer, updateLayer]);

    const removeGameHistoryPartTarget = React.useCallback((target: GameHistoryPartTarget) => {
        if (selectedLayer?.type !== "image" || !selectedLayer.gameHistoryStyle) return;
        updateLayer(selectedLayer.id, {
            gameHistoryStyle: {
                ...selectedLayer.gameHistoryStyle,
                parts: (selectedLayer.gameHistoryStyle.parts ?? []).filter((part) =>
                    !(part.role === target.role && part.setIndex === target.setIndex)),
            },
        });
    }, [selectedLayer, updateLayer]);

    const addBinding = () => {
        const layer = createBindingLayer(bindingToAdd, maxZ(project) + 1);
        applyProjectChange(
            (current) => ({...current, layers: [...current.layers, layer]}),
        );
        setSelection([layer.id], layer.id);
    };

    const addImages = async (files: FileList | null) => {
        if (!files?.length) return;
        const nextAssets = {...assets};
        const nextLayers = [...project.layers];
        const nextProjectAssets = [...project.assets];
        let zIndex = maxZ(project) + 1;
        let lastAddedId: string | null = null;

        for (const file of Array.from(files)) {
            const lowerName = file.name.toLowerCase();
            const isSvg = file.type === "image/svg+xml" || lowerName.endsWith(".svg");
            const isPng = file.type === "image/png" || lowerName.endsWith(".png");
            if (!isPng && !isSvg) {
                setMessage(`${file.name}: PNGまたはSVGのみ追加できます。`);
                continue;
            }
            try {
                const {width, height} = await readImageSize(file);
                const path = makeUniqueAssetPath(file.name, nextAssets);
                const previewUrl = URL.createObjectURL(file);
                nextAssets[path] = {path, blob: file, previewUrl};
                const layer: ImageLayer = {
                    id: createId(),
                    name: file.name.replace(/\.(png|svg)$/i, ""),
                    type: "image",
                    asset: path,
                    objectFit: "contain",
                    x: Math.round((1920 - width) / 2),
                    y: Math.round((1080 - height) / 2),
                    width,
                    height,
                    rotation: 0,
                    opacity: 1,
                    zIndex: zIndex++,
                    visible: true,
                    animation: {preset: "fade", durationMs: 450, delayMs: 0},
                    animations: createDefaultAnimations(0),
                    events: createDefaultEvents(0),
                };
                nextLayers.push(layer);
                nextProjectAssets.push({
                    id: createId(),
                    kind: "image",
                    path,
                    mediaType: isSvg ? "image/svg+xml" : "image/png",
                });
                lastAddedId = layer.id;
            } catch {
                setMessage(`${file.name}: 画像を読み込めませんでした。`);
            }
        }
        setAssets(nextAssets);
        applyProjectChange(
            (current) => ({
                ...current,
                assets: nextProjectAssets,
                layers: nextLayers,
            }),
            "画像レイヤーを追加しました。",
        );
        if (lastAddedId) setSelection([lastAddedId], lastAddedId);
        if (imageInputRef.current) imageInputRef.current.value = "";
        if (svgInputRef.current) svgInputRef.current.value = "";
    };

    const deleteSelected = React.useCallback(() => {
        if (!selectedIds.length) return;
        const ids = new Set(selectedIds);
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.filter((layer) => !ids.has(layer.id)),
            autoLayouts: current.autoLayouts?.map((layout) => ({
                ...layout,
                layerIds: layout.layerIds.filter((id) => !ids.has(id)),
            })),
        }));
        setSelection([], null);
    }, [applyProjectChange, selectedIds, setSelection]);

    const duplicateSelected = () => {
        if (!selectedLayers.length) return;
        let zIndex = maxZ(project) + 1;
        const copies = selectedLayers
            .sort((a, b) => a.zIndex - b.zIndex)
            .map((layer) => cloneLayer(layer, zIndex++));
        applyProjectChange(
            (current) => ({...current, layers: [...current.layers, ...copies]}),
        );
        setSelection(copies.map((copy) => copy.id), copies[copies.length - 1]?.id ?? null);
    };

    const groupSelected = () => {
        if (selectedIds.length < 2) return;
        const groupId = createId();
        applyProjectChange((current) => ({
            ...current,
            groups: [
                ...(current.groups ?? []),
                {
                    id: groupId,
                    name: `グループ ${(current.groups?.length ?? 0) + 1}`,
                    collapsed: false,
                },
            ],
            layers: current.layers.map((layer) =>
                selectedIds.includes(layer.id)
                    ? ({...layer, groupId} as DesignerLayer)
                    : layer,
            ),
        }));
    };

    const toggleGroupCollapsed = React.useCallback((groupId: string) => {
        applyProjectChange((current) => ({
            ...current,
            groups: (current.groups ?? []).map((group) =>
                group.id === groupId
                    ? {...group, collapsed: !group.collapsed}
                    : group,
            ),
        }));
    }, [applyProjectChange]);

    const updateGroupName = React.useCallback((groupId: string, name: string) => {
        applyProjectChange((current) => ({
            ...current,
            groups: (current.groups ?? []).map((group) =>
                group.id === groupId ? {...group, name} : group,
            ),
        }));
    }, [applyProjectChange]);

    const setOnlyLayersVisible = React.useCallback((visibleIds: string[]) => {
        const visible = new Set(visibleIds);
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.map((layer) => ({
                ...layer,
                visible: visible.has(layer.id),
            } as DesignerLayer)),
        }));
    }, [applyProjectChange]);

    const toggleLayerVisible = React.useCallback((
        layerId: string,
        nextVisible: boolean,
    ) => {
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.map((layer) =>
                layer.id === layerId
                    ? ({...layer, visible: nextVisible} as DesignerLayer)
                    : layer,
            ),
        }));
    }, [applyProjectChange]);

    const toggleGroupVisible = React.useCallback((
        groupLayerIds: string[],
        nextVisible: boolean,
    ) => {
        const ids = new Set(groupLayerIds);
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.map((layer) =>
                ids.has(layer.id)
                    ? ({...layer, visible: nextVisible} as DesignerLayer)
                    : layer,
            ),
        }));
    }, [applyProjectChange]);

    const ungroupSelected = () => {
        const groupIds = new Set(
            selectedLayers
                .map((layer) => layer.groupId)
                .filter(Boolean) as string[],
        );
        if (!groupIds.size) return;
        applyProjectChange((current) => ({
            ...current,
            groups: (current.groups ?? []).filter((group) => !groupIds.has(group.id)),
            layers: current.layers.map((layer) =>
                layer.groupId && groupIds.has(layer.groupId)
                    ? ({...layer, groupId: undefined} as DesignerLayer)
                    : layer,
            ),
        }));
    };

    const loadDefaultTemplate = () => {
        if (project.layers.length && !window.confirm("現在の編集内容を破棄しますか？")) return;
        revokeAssetUrls(assets);
        const template = defaultTemplateProject(standardTemplate);
        setAssets({});
        resetProject(template, "標準テンプレートを読み込みました。");
        setSelection([template.layers[0].id], template.layers[0].id);
        setCurrentSlug(null);
    };

    const selectAllLayers = React.useCallback(() => {
        const ids = project.layers.map((layer) => layer.id);
        setSelection(ids, ids[0] ?? null);
    }, [project.layers, setSelection]);

    const receiveDesignerImport = React.useCallback(async (
        notification?: {importId?: string},
    ) => {
        try {
            const importId = notification?.importId;
            if (importId && handledImportIdsRef.current.has(importId)) return;
            if (importId) handledImportIdsRef.current.add(importId);
            const imported = importId
                ? await loadDesignerImport(importId)
                : await loadLatestFigmaImport();
            if (!importId) {
                revokeAssetUrls(assets);
                resetProject(
                    imported.project,
                    "Figmaプラグインから自動受信しました。",
                );
                setAssets(imported.assets);
                setSelection(
                    imported.project.layers[0] ? [imported.project.layers[0].id] : [],
                    imported.project.layers[0]?.id ?? null,
                );
                setCurrentSlug(null);
                return;
            }
            const merged = mergeImportedProject(project, imported.project);
            const warnings = (
                imported as {
                    report?: {warnings?: string[]};
                }
            ).report?.warnings ?? [];
            const summary = [
                `${imported.project.layers.length}レイヤーを取り込みます。`,
                merged.conflicts.length
                    ? `${merged.conflicts.length}件の競合はAutona側を維持します。`
                    : "競合はありません。",
                warnings.length ? `警告: ${warnings.join(" / ")}` : "",
            ].filter(Boolean).join("\n");
            if (!window.confirm(`${summary}\n\n適用しますか？`)) return;
            resetProject(merged.project, "外部制作ファイルを再インポートしました。");
            setAssets({...assets, ...imported.assets});
            setSelection(
                merged.project.layers[0] ? [merged.project.layers[0].id] : [],
                merged.project.layers[0]?.id ?? null,
            );
            setCurrentSlug(null);
        } catch (error: any) {
            setMessage(error?.message || "外部制作ファイルを受信できませんでした。");
        }
    }, [assets, project, resetProject, setSelection]);

    React.useEffect(() => {
        if (!desktopMode) return undefined;
        const socket = socketConnection.instance();
        socket.on("designer_import_received", receiveDesignerImport);
        return () => {
            socket.off("designer_import_received", receiveDesignerImport);
        };
    }, [desktopMode, receiveDesignerImport]);

    const reorderSelected = (direction: -1 | 1) => {
        if (!selectedLayer) return;
        const activeKind = selectedGroupId ? "group" : "layer";
        const activeId = selectedGroupId ?? selectedLayer.id;
        const index = panelItems.findIndex((item) =>
            item.kind === activeKind &&
            (item.kind === "group" ? item.group.id : item.layer.id) === activeId,
        );
        if (index < 0) return;
        const targetIndex = direction > 0 ? index - 1 : index + 1;
        if (targetIndex < 0 || targetIndex >= panelItems.length) return;
        const nextItems = [...panelItems];
        const [item] = nextItems.splice(index, 1);
        nextItems.splice(targetIndex, 0, item);
        reorderLayerIds(nextItems.flatMap((nextItem) =>
            nextItem.kind === "group"
                ? nextItem.layers.map((layer) => layer.id)
                : [nextItem.layer.id],
        ));
    };

    const reorderLayerIds = React.useCallback((orderIds: string[]) => {
        const nextZ = new Map(orderIds.map((id, index) => [id, orderIds.length - index]));
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.map((layer) => ({
                ...layer,
                zIndex: nextZ.get(layer.id) ?? layer.zIndex,
            } as DesignerLayer)),
        }));
    }, [applyProjectChange]);

    const startLayerDrag = React.useCallback((
        event: React.PointerEvent,
        layerId: string,
    ) => {
        event.preventDefault();
        event.stopPropagation();
        const row = (event.currentTarget as HTMLElement)
            .closest(".designer-layer-row") as HTMLElement | null;
        const rect = row?.getBoundingClientRect();
        const listRect = layerListRef.current?.getBoundingClientRect();
        if (!rect || !listRect) return;
        const initialOrder = orderedLayers.map((layer) => layer.id);
        const sourceIndex = initialOrder.indexOf(layerId);
        if (sourceIndex < 0) return;
        let finalOrder = initialOrder;
        const rowElements = initialOrder
            .map((id) => layerRowRefs.current[id])
            .filter(Boolean) as HTMLButtonElement[];
        const firstTop = rowElements[0]?.getBoundingClientRect().top ?? rect.top;
        const secondTop = rowElements[1]?.getBoundingClientRect().top;
        const geometry: LayerDragGeometry = {
            listTop: firstTop,
            rowHeight: rect.height,
            rowStep: secondTop ? secondTop - firstTop : rect.height + 4,
        };

        setLayerDrag({
            sourceId: layerId,
            pointerY: event.clientY,
            offsetY: event.clientY - rect.top,
            left: rect.left,
            width: rect.width,
            orderIds: initialOrder,
        });

        const move = (moveEvent: PointerEvent) => {
            setLayerDrag((current) => {
                if (!current) return current;
                const draggedCenterY = moveEvent.clientY -
                    current.offsetY + geometry.rowHeight / 2;
                const relativeY = draggedCenterY - geometry.listTop;
                const rawIndex = Math.floor(relativeY / geometry.rowStep);
                const targetIndex = Math.max(
                    0,
                    Math.min(initialOrder.length - 1, rawIndex),
                );
                const nextOrder = initialOrder.filter((id) => id !== layerId);
                nextOrder.splice(targetIndex, 0, layerId);
                finalOrder = nextOrder;
                return {
                    ...current,
                    pointerY: moveEvent.clientY,
                    orderIds: nextOrder,
                };
            });
        };

        const finish = () => {
            if (finalOrder.join("\0") !== initialOrder.join("\0")) {
                reorderLayerIds(finalOrder);
            }
            setLayerDrag(null);
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", finish);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", finish);
    }, [orderedLayers, reorderLayerIds]);

    const startPanelDrag = React.useCallback((
        event: React.PointerEvent,
        sourceKey: string,
    ) => {
        event.preventDefault();
        event.stopPropagation();
        const row = (event.currentTarget as HTMLElement)
            .closest(".designer-layer-panel-row") as HTMLButtonElement | null;
        const rect = row?.getBoundingClientRect();
        if (!rect) return;
        const initialItems = panelItems;
        const initialKeys = initialItems.map(panelItemKey);
        const sourceIndex = initialKeys.indexOf(sourceKey);
        if (sourceIndex < 0) return;
        const rowElements = initialKeys
            .map((key) => panelRowRefs.current[key])
            .filter(Boolean) as HTMLButtonElement[];
        const firstTop = rowElements[0]?.getBoundingClientRect().top ?? rect.top;
        const secondTop = rowElements[1]?.getBoundingClientRect().top;
        const geometry: LayerDragGeometry = {
            listTop: firstTop,
            rowHeight: rect.height,
            rowStep: secondTop ? secondTop - firstTop : rect.height + 4,
        };
        let finalKeys = initialKeys;

        setPanelDrag({
            sourceKey,
            pointerY: event.clientY,
            offsetY: event.clientY - rect.top,
            left: rect.left,
            width: rect.width,
            itemKeys: initialKeys,
        });

        const move = (moveEvent: PointerEvent) => {
            setPanelDrag((current) => {
                if (!current) return current;
                const draggedCenterY = moveEvent.clientY -
                    current.offsetY + geometry.rowHeight / 2;
                const relativeY = draggedCenterY - geometry.listTop;
                const rawIndex = Math.floor(relativeY / geometry.rowStep);
                const targetIndex = Math.max(
                    0,
                    Math.min(initialKeys.length - 1, rawIndex),
                );
                const nextKeys = initialKeys.filter((key) => key !== sourceKey);
                nextKeys.splice(targetIndex, 0, sourceKey);
                finalKeys = nextKeys;
                return {
                    ...current,
                    pointerY: moveEvent.clientY,
                    itemKeys: nextKeys,
                };
            });
        };

        const finish = () => {
            if (finalKeys.join("\0") !== initialKeys.join("\0")) {
                const byKey = new Map(initialItems.map((item) => [panelItemKey(item), item]));
                reorderLayerIds(finalKeys.flatMap((key) => {
                    const item = byKey.get(key);
                    if (!item) return [];
                    return item.kind === "group"
                        ? item.layers.map((layer) => layer.id)
                        : [item.layer.id];
                }));
            }
            setPanelDrag(null);
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", finish);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", finish);
    }, [panelItems, reorderLayerIds]);

    const selectLayerFromPanel = React.useCallback((
        event: React.MouseEvent,
        layerId: string,
    ) => {
        const orderedIds = orderedLayers.map((layer) => layer.id);
        if (event.shiftKey && primarySelectedId) {
            const start = orderedIds.indexOf(primarySelectedId);
            const end = orderedIds.indexOf(layerId);
            if (start >= 0 && end >= 0) {
                const [from, to] = start < end ? [start, end] : [end, start];
                const ids = orderedIds.slice(from, to + 1);
                setSelection(ids, layerId);
                return;
            }
        }
        if (event.ctrlKey || event.metaKey) {
            const exists = selectedIds.includes(layerId);
            const ids = exists
                ? selectedIds.filter((id) => id !== layerId)
                : [...selectedIds, layerId];
            setSelection(ids, exists ? ids[ids.length - 1] ?? null : layerId);
            return;
        }
        setSelection([layerId], layerId);
    }, [orderedLayers, primarySelectedId, selectedIds, setSelection]);

    const startNameEdit = React.useCallback((layer: DesignerLayer) => {
        setEditingLayerId(layer.id);
        setEditingLayerName(layer.name);
        setEditingGroupId(null);
        setEditingGroupName("");
        setSelection([layer.id], layer.id);
    }, [setSelection]);

    const commitNameEdit = React.useCallback(() => {
        if (!editingLayerId) return;
        const nextName = editingLayerName.trim();
        if (nextName) updateLayer(editingLayerId, {name: nextName});
        setEditingLayerId(null);
        setEditingLayerName("");
    }, [editingLayerId, editingLayerName, updateLayer]);

    const cancelNameEdit = React.useCallback(() => {
        setEditingLayerId(null);
        setEditingLayerName("");
    }, []);

    const startGroupNameEdit = React.useCallback((groupId: string, name: string) => {
        setEditingLayerId(null);
        setEditingLayerName("");
        setEditingGroupId(groupId);
        setEditingGroupName(name);
    }, []);

    const commitGroupNameEdit = React.useCallback(() => {
        if (!editingGroupId) return;
        const nextName = editingGroupName.trim();
        if (nextName) updateGroupName(editingGroupId, nextName);
        setEditingGroupId(null);
        setEditingGroupName("");
    }, [editingGroupId, editingGroupName, updateGroupName]);

    const cancelGroupNameEdit = React.useCallback(() => {
        setEditingGroupId(null);
        setEditingGroupName("");
    }, []);

    const newProject = () => {
        if (project.layers.length && !window.confirm("現在の編集内容を破棄しますか？")) return;
        revokeAssetUrls(assets);
        setAssets({});
        resetProject(createEmptyProject(), "新規プロジェクト");
        setSelection([], null);
        setCurrentSlug(null);
    };

    const openProject = async () => {
        try {
            const projects = await listDesktopProjects();
            setProjectChoices(projects);
            setProjectDialogOpen(true);
            if (!projects.length) {
                setMessage("保存済みのカスタムデザインはありません。");
            }
        } catch (error: any) {
            setMessage(error?.message || "プロジェクト一覧を読み込めませんでした。");
        }
    };

    const selectProject = async (summary: DesignerProjectSummary) => {
        try {
            const loaded = await loadDesktopProject(summary.slug);
            revokeAssetUrls(assets);
            resetProject(loaded.project, `layout/custom/${loaded.slug} を読み込みました。`);
            setAssets(loaded.assets);
            setSelection([], null);
            setCurrentSlug(loaded.slug);
            setProjectDialogOpen(false);
        } catch (error: any) {
            setMessage(error?.message || "プロジェクトを読み込めませんでした。");
        }
    };

    const deleteProject = async (summary: DesignerProjectSummary) => {
        if (!window.confirm(
            `「${summary.name}」を削除しますか？\nこの操作は取り消せません。`,
        )) return;
        try {
            await deleteDesktopProject(summary.slug);
            setProjectChoices((choices) =>
                choices.filter((choice) => choice.slug !== summary.slug));
            if (currentSlug === summary.slug) {
                revokeAssetUrls(assets);
                setAssets({});
                resetProject(createEmptyProject(), "新規プロジェクト");
                setSelection([], null);
                setCurrentSlug(null);
            }
            setMessage(`layout/custom/${summary.slug} を削除しました。`);
        } catch (error: any) {
            setMessage(error?.message || "プロジェクトを削除できませんでした。");
        }
    };

    const saveProject = async () => {
        try {
            const result = await saveDesktopProject(project, assets);
            setCurrentSlug(result.slug);
            setMessage(`${result.path} に保存しました。`);
        } catch (error: any) {
            setMessage(error?.message || "保存できませんでした。");
        }
    };

    if (!desktopMode) {
        return (
            <Box className="designer-desktop-required">
                <Typography variant="h5">Scoreboard Designer</Typography>
                <Typography color="text.secondary">
                    DesignerはAutonaデスクトップアプリのBOARDタブから開いてください。
                </Typography>
            </Box>
        );
    }

    return (
        <Box className="scoreboard-designer">
            <AppBar position="static">
                <Toolbar variant="dense" className="designer-toolbar">
                    <Typography variant="subtitle1" sx={{mr: 1}}>
                        Scoreboard Designer
                    </Typography>
                    <Button
                        id="designer-import-button"
                        aria-controls={importAnchorEl ? "designer-import-menu" : undefined}
                        aria-haspopup="true"
                        aria-expanded={importAnchorEl ? "true" : undefined}
                        variant={importAnchorEl ? "contained" : "text"}
                        onClick={(event) => setImportAnchorEl(event.currentTarget)}
                    >
                        Import
                    </Button>
                    <Menu
                        id="designer-import-menu"
                        anchorEl={importAnchorEl}
                        open={Boolean(importAnchorEl)}
                        onClose={closeImportMenu}
                        MenuListProps={{
                            "aria-labelledby": "designer-import-button",
                        }}
                    >
                        <MenuItem onClick={() => {
                            closeImportMenu();
                            imageInputRef.current?.click();
                        }}>
                            <AddPhotoAlternate fontSize="small" sx={{mr: 1}} />
                            PNGを追加
                        </MenuItem>
                        <MenuItem onClick={() => {
                            closeImportMenu();
                            svgInputRef.current?.click();
                        }}>
                            <AddPhotoAlternate fontSize="small" sx={{mr: 1}} />
                            SVGを追加
                        </MenuItem>
                    </Menu>
                    <input
                        ref={imageInputRef}
                        type="file"
                        accept="image/png,.png"
                        multiple
                        hidden
                        onChange={(event) => addImages(event.target.files)}
                    />
                    <input
                        ref={svgInputRef}
                        type="file"
                        accept="image/svg+xml,.svg"
                        multiple
                        hidden
                        onChange={(event) => addImages(event.target.files)}
                    />
                    <ButtonGroup size="small">
                        <Tooltip title="元に戻す">
                            <span>
                                <IconButton
                                    size="small"
                                    disabled={!canUndo}
                                    onClick={undoProject}
                                    aria-label="元に戻す"
                                >
                                    <Undo fontSize="small" />
                                </IconButton>
                            </span>
                        </Tooltip>
                        <Tooltip title="やり直す">
                            <span>
                                <IconButton
                                    size="small"
                                    disabled={!canRedo}
                                    onClick={redoProject}
                                    aria-label="やり直す"
                                >
                                    <Redo fontSize="small" />
                                </IconButton>
                            </span>
                        </Tooltip>
                    </ButtonGroup>
                    <Button onClick={newProject}>新規</Button>
                    <Button startIcon={<FolderOpen />} onClick={openProject}>開く</Button>
                    <Button startIcon={<Save />} variant="contained" onClick={saveProject}>
                        保存
                    </Button>
                    <ButtonGroup size="small" variant="outlined" aria-label="編集モード">
                        <Button
                            variant={editorMode === "design" ? "contained" : "outlined"}
                            onClick={() => setEditorMode("design")}
                        >
                            Design
                        </Button>
                        <Button
                            variant={editorMode === "animate" ? "contained" : "outlined"}
                            onClick={() => setEditorMode("animate")}
                        >
                            Animate
                        </Button>
                    </ButtonGroup>
                    <Button
                        startIcon={<PlayArrow />}
                        onClick={() => setAnimationNonce((value) => value + 1)}
                    >
                        アニメーション
                    </Button>
                </Toolbar>
            </AppBar>

            <Box className="designer-workspace">
                <Box component="aside" className="designer-sidebar designer-left-sidebar">
                    <TextField
                        size="small"
                        label="デザイン名"
                        value={project.name}
                        onChange={(event) => {
                            const name = event.target.value;
                            applyProjectChange((current) => ({...current, name}));
                        }}
                        fullWidth
                    />
                    <Box className="designer-preview-data">
                        <Typography variant="subtitle2">プレビューデータ</Typography>
                        <Box className="designer-data-toggle">
                            <Typography
                                variant="body2"
                                color={sampleMode ? "text.primary" : "text.secondary"}
                            >
                                サンプル
                            </Typography>
                            <Switch
                                size="small"
                                checked={!sampleMode}
                                onChange={(event) => setSampleMode(!event.target.checked)}
                                slotProps={{
                                    input: {
                                        "aria-label": "実データを使用",
                                        role: "switch",
                                    },
                                }}
                            />
                            <Typography
                                variant="body2"
                                color={!sampleMode ? "text.primary" : "text.secondary"}
                            >
                                実データ
                            </Typography>
                        </Box>
                        {!sampleMode && (
                            <Typography
                                variant="caption"
                                color="text.secondary"
                                className="designer-data-toggle-status"
                            >
                                {hasLiveState
                                    ? websocketStatus
                                    : `待機中 (${websocketStatus})`}
                            </Typography>
                        )}
                    </Box>
                    <Divider />
                    <Typography variant="caption" color="text.secondary">
                        標準テンプレート
                    </Typography>
                    <Stack direction="row" gap={1}>
                        <Select
                            size="small"
                            sx={{flex: 1, minWidth: 0}}
                            value={standardTemplate}
                            onChange={(event) =>
                                setStandardTemplate(event.target.value as StandardTemplateKey)
                            }
                            inputProps={{"aria-label": "標準テンプレート"}}
                        >
                            {STANDARD_TEMPLATES.map((template) => (
                                <MenuItem key={template.key} value={template.key}>
                                    {template.name}
                                </MenuItem>
                            ))}
                        </Select>
                        <Button variant="outlined" onClick={loadDefaultTemplate}>
                            読込
                        </Button>
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                        動的要素
                    </Typography>
                    <Stack direction="row" gap={1}>
                        <Select
                            size="small"
                            sx={{flex: 1, minWidth: 0}}
                            value={bindingToAdd}
                            onChange={(event) => setBindingToAdd(event.target.value as BindingKey)}
                        >
                            {BINDINGS.map((binding) => {
                                const count = bindingCounts.get(binding) ?? 0;
                                return (
                                <MenuItem
                                    key={binding}
                                    value={binding}
                                    className={count ? "designer-binding-used" : ""}
                                >
                                    <Stack
                                        direction="row"
                                        alignItems="center"
                                        justifyContent="space-between"
                                        width="100%"
                                        gap={1}
                                    >
                                        <span>{BINDING_LABELS[binding]}</span>
                                        {count > 0 && (
                                            <Typography variant="caption" color="text.secondary">
                                                追加済み {count}
                                            </Typography>
                                        )}
                                    </Stack>
                                </MenuItem>
                                );
                            })}
                        </Select>
                        <Button variant="contained" onClick={addBinding}>追加</Button>
                    </Stack>
                    <Divider />
                    <Stack direction="row" alignItems="center">
                        <Typography variant="subtitle2" sx={{flex: 1}}>レイヤー</Typography>
                        <ButtonGroup size="small">
                            <Tooltip title="背面へ">
                                <span>
                                    <IconButton
                                        size="small"
                                        aria-label="背面へ"
                                        disabled={!selectedLayer}
                                        onClick={() => reorderSelected(-1)}
                                    ><ArrowDownward fontSize="small" /></IconButton>
                                </span>
                            </Tooltip>
                            <Tooltip title="前面へ">
                                <span>
                                    <IconButton
                                        size="small"
                                        aria-label="前面へ"
                                        disabled={!selectedLayer}
                                        onClick={() => reorderSelected(1)}
                                    ><ArrowUpward fontSize="small" /></IconButton>
                                </span>
                            </Tooltip>
                            <Tooltip title="複製">
                                <span>
                                    <IconButton
                                        size="small"
                                        aria-label="複製"
                                        disabled={!selectedLayers.length}
                                        onClick={duplicateSelected}
                                    ><ContentCopy fontSize="small" /></IconButton>
                                </span>
                            </Tooltip>
                            <Tooltip title="削除">
                                <span>
                                    <IconButton
                                        size="small"
                                        aria-label="削除"
                                        disabled={!selectedIds.length}
                                        onClick={deleteSelected}
                                    ><Delete fontSize="small" /></IconButton>
                                </span>
                            </Tooltip>
                        </ButtonGroup>
                    </Stack>
                    <Stack direction="row" gap={1}>
                        <Button
                            size="small"
                            fullWidth
                            variant="outlined"
                            disabled={selectedIds.length < 2}
                            onClick={groupSelected}
                        >
                            グループ化
                        </Button>
                        <Button
                            size="small"
                            fullWidth
                            variant="outlined"
                            disabled={!selectedLayers.some((layer) => layer.groupId)}
                            onClick={ungroupSelected}
                        >
                            解除
                        </Button>
                    </Stack>
                    <Box
                        className="designer-layer-list"
                        ref={layerListRef}
                    >
                        {panelItems.map((item) => {
                            if (item.kind === "group") {
                                const layerIds = item.layers.map((layer) => layer.id);
                                const itemKey = panelItemKey(item);
                                const groupVisible = item.layers.some((layer) => layer.visible);
                                const groupSelected = layerIds.length > 0 &&
                                    layerIds.every((id) => selectedIds.includes(id));
                                const groupPrimary = Boolean(
                                    primarySelectedId && layerIds.includes(primarySelectedId),
                                );
                                return (
                                    <React.Fragment key={item.group.id}>
                                        <button
                                            type="button"
                                            ref={(element) => {
                                                panelRowRefs.current[itemKey] = element;
                                            }}
                                            className={`designer-layer-panel-row designer-layer-group-row${
                                                groupSelected ? " selected" : ""
                                            }${
                                                groupPrimary ? " primary" : ""
                                            }${
                                                panelDrag?.sourceKey === itemKey ? " drag-placeholder" : ""
                                            }`}
                                            onClick={() => {
                                                setSelection(layerIds, layerIds[0] ?? null);
                                            }}
                                            onDoubleClick={() =>
                                                startGroupNameEdit(item.group.id, item.group.name)
                                            }
                                        >
                                            <span
                                                className="designer-layer-drag"
                                                onPointerDown={(event) => startPanelDrag(event, itemKey)}
                                                aria-label={`${item.group.name}を並べ替え`}
                                                role="button"
                                            >
                                                <DragIndicator fontSize="small" />
                                            </span>
                                            <IconButton
                                                size="small"
                                                component="span"
                                                aria-label={`${item.group.name}を${
                                                    item.collapsed ? "展開" : "折りたたみ"
                                                }`}
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    toggleGroupCollapsed(item.group.id);
                                                }}
                                            >
                                                {item.collapsed
                                                    ? <KeyboardArrowRight fontSize="small" />
                                                    : <KeyboardArrowDown fontSize="small" />}
                                            </IconButton>
                                            {item.collapsed
                                                ? <Folder fontSize="small" />
                                                : <FolderOpen fontSize="small" />}
                                            {editingGroupId === item.group.id ? (
                                                <TextField
                                                    size="small"
                                                    className="designer-layer-group-name-editor"
                                                    value={editingGroupName}
                                                    autoFocus
                                                    onClick={(event) => event.stopPropagation()}
                                                    onDoubleClick={(event) => event.stopPropagation()}
                                                    onChange={(event) => setEditingGroupName(event.target.value)}
                                                    onBlur={commitGroupNameEdit}
                                                    onKeyDown={(event) => {
                                                        if (event.key === "Enter") commitGroupNameEdit();
                                                        if (event.key === "Escape") cancelGroupNameEdit();
                                                    }}
                                                />
                                            ) : (
                                                <span className="designer-layer-group-name">
                                                    {item.group.name}
                                                </span>
                                            )}
                                            <IconButton
                                                size="small"
                                                component="span"
                                                aria-label={`${item.group.name}を${
                                                    groupVisible ? "非表示" : "表示"
                                                }`}
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    if (event.altKey) {
                                                        setOnlyLayersVisible(layerIds);
                                                        return;
                                                    }
                                                    toggleGroupVisible(layerIds, !groupVisible);
                                                }}
                                            >
                                                {groupVisible
                                                    ? <Visibility fontSize="small" />
                                                    : <VisibilityOff fontSize="small" />}
                                            </IconButton>
                                        </button>
                                        {!item.collapsed && item.layers.map((layer) => (
                                            <button
                                                type="button"
                                                key={layer.id}
                                                ref={(element) => {
                                                    layerRowRefs.current[layer.id] = element;
                                                    panelRowRefs.current[`layer:${layer.id}`] = element;
                                                }}
                                                data-layer-id={layer.id}
                                                className={`designer-layer-panel-row designer-layer-row designer-layer-row-child${
                                                    selectedIds.includes(layer.id) ? " selected" : ""
                                                }${
                                                    primarySelectedId === layer.id ? " primary" : ""
                                                }${
                                                    layerDrag?.sourceId === layer.id ? " drag-placeholder" : ""
                                                }`}
                                                onClick={(event) => selectLayerFromPanel(event, layer.id)}
                                                onDoubleClick={() => startNameEdit(layer)}
                                            >
                                                <span
                                                    className="designer-layer-drag"
                                                    onPointerDown={(event) => startLayerDrag(event, layer.id)}
                                                    aria-label={`${layer.name}を並べ替え`}
                                                    role="button"
                                                >
                                                    <DragIndicator fontSize="small" />
                                                </span>
                                                <span className={`designer-layer-type type-${layer.type}`}>
                                                    {layer.type}
                                                </span>
                                                {editingLayerId === layer.id ? (
                                                    <TextField
                                                        size="small"
                                                        className="designer-layer-name-editor"
                                                        value={editingLayerName}
                                                        autoFocus
                                                        onClick={(event) => event.stopPropagation()}
                                                        onDoubleClick={(event) => event.stopPropagation()}
                                                        onChange={(event) => setEditingLayerName(event.target.value)}
                                                        onBlur={commitNameEdit}
                                                        onKeyDown={(event) => {
                                                            if (event.key === "Enter") commitNameEdit();
                                                            if (event.key === "Escape") cancelNameEdit();
                                                        }}
                                                    />
                                                ) : (
                                                    <span className="designer-layer-name">{layer.name}</span>
                                                )}
                                                <IconButton
                                                    size="small"
                                                    component="span"
                                                    aria-label={`${layer.name}を${
                                                        layer.visible ? "非表示" : "表示"
                                                    }`}
                                                    onClick={(event) => {
                                                        event.stopPropagation();
                                                        if (event.altKey) {
                                                            setOnlyLayersVisible([layer.id]);
                                                            return;
                                                        }
                                                        toggleLayerVisible(layer.id, !layer.visible);
                                                    }}
                                                >
                                                    {layer.visible
                                                        ? <Visibility fontSize="small" />
                                                        : <VisibilityOff fontSize="small" />}
                                                </IconButton>
                                            </button>
                                        ))}
                                    </React.Fragment>
                                );
                            }
                            const layer = item.layer;
                            const itemKey = panelItemKey(item);
                            return (
                                <button
                                    type="button"
                                    key={layer.id}
                                    ref={(element) => {
                                        layerRowRefs.current[layer.id] = element;
                                        panelRowRefs.current[itemKey] = element;
                                    }}
                                    data-layer-id={layer.id}
                                    className={`designer-layer-panel-row designer-layer-row${
                                        selectedIds.includes(layer.id) ? " selected" : ""
                                    }${
                                        primarySelectedId === layer.id ? " primary" : ""
                                    }${
                                        layerDrag?.sourceId === layer.id ? " drag-placeholder" : ""
                                    }`}
                                    onClick={(event) => selectLayerFromPanel(event, layer.id)}
                                    onDoubleClick={() => startNameEdit(layer)}
                                >
                                    <span
                                        className="designer-layer-drag"
                                        onPointerDown={(event) => startLayerDrag(event, layer.id)}
                                        aria-label={`${layer.name}を並べ替え`}
                                        role="button"
                                    >
                                        <DragIndicator fontSize="small" />
                                    </span>
                                    <span className={`designer-layer-type type-${layer.type}`}>
                                        {layer.type}
                                    </span>
                                    {editingLayerId === layer.id ? (
                                        <TextField
                                            size="small"
                                            className="designer-layer-name-editor"
                                            value={editingLayerName}
                                            autoFocus
                                            onClick={(event) => event.stopPropagation()}
                                            onDoubleClick={(event) => event.stopPropagation()}
                                            onChange={(event) => setEditingLayerName(event.target.value)}
                                            onBlur={commitNameEdit}
                                            onKeyDown={(event) => {
                                                if (event.key === "Enter") commitNameEdit();
                                                if (event.key === "Escape") cancelNameEdit();
                                            }}
                                        />
                                    ) : (
                                        <span className="designer-layer-name">{layer.name}</span>
                                    )}
                                    <IconButton
                                        size="small"
                                        component="span"
                                        aria-label={`${layer.name}を${
                                            layer.visible ? "非表示" : "表示"
                                        }`}
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            if (event.altKey) {
                                                setOnlyLayersVisible([layer.id]);
                                                return;
                                            }
                                            toggleLayerVisible(layer.id, !layer.visible);
                                        }}
                                    >
                                        {layer.visible
                                            ? <Visibility fontSize="small" />
                                            : <VisibilityOff fontSize="small" />}
                                    </IconButton>
                                </button>
                            );
                        })}
                        {!project.layers.length && (
                            <Typography variant="body2" color="text.secondary" p={1}>
                                PNGまたは動的要素を追加してください。
                            </Typography>
                        )}
                    </Box>
                </Box>
                {floatingLayer && layerDrag && (
                    <div
                        className="designer-layer-row dragging"
                        style={{
                            left: layerDrag.left,
                            top: layerDrag.pointerY - layerDrag.offsetY,
                            width: layerDrag.width,
                        }}
                    >
                        <span className="designer-layer-drag">
                            <DragIndicator fontSize="small" />
                        </span>
                        <span className={`designer-layer-type type-${floatingLayer.type}`}>
                            {floatingLayer.type}
                        </span>
                        <span className="designer-layer-name">{floatingLayer.name}</span>
                        <span />
                    </div>
                )}

                <DesignerCanvas
                    project={project}
                    assets={assets}
                    previewState={previewState}
                    selectedIds={selectedIds}
                    primarySelectedId={primarySelectedId}
                    animationNonce={animationNonce}
                    animationTrack={animationTrack}
                    onSelectionChange={setSelection}
                    onLayerChange={updateLayer}
                    onDeleteSelected={deleteSelected}
                    onSelectAll={selectAllLayers}
                    gameHistoryPartEditLayerId={gameHistoryPartEditLayerId}
                    selectedGameHistoryPartPath={selectedGameHistoryPartPath}
                    onGameHistoryPartSelect={selectGameHistoryPart}
                    onInteractionStart={beginCanvasInteraction}
                    onInteractionEnd={endCanvasInteraction}
                />

                <Box component="aside" className="designer-sidebar designer-right-sidebar">
                    <Typography variant="subtitle2">
                        {editorMode === "design" ? "プロパティ" : "アニメーション"}
                    </Typography>
                    <Divider />
                    {editorMode === "design" ? (
                        <LayerInspector
                            layer={selectedLayer}
                            gameHistoryAssignments={gameHistoryAssignments}
                            onGameHistoryAssignmentSelect={selectGameHistoryAssignment}
                            // Any SVG image can be used as an explicit Game-History
                            // lamp sheet, including SVGs added directly in Designer.
                            allowGameHistory={Boolean(
                                selectedLayer?.type === "image" &&
                                /\.svg$/i.test(selectedLayer.asset),
                            )}
                            gameHistoryPartEditing={
                                gameHistoryPartEditLayerId === selectedLayer?.id
                            }
                            selectedGameHistoryPartPath={selectedGameHistoryPartPath}
                            onGameHistoryPartEditingChange={updateGameHistoryPartEditing}
                            onGameHistoryPartTargetAssign={assignSelectedGameHistoryPart}
                            onGameHistoryPartTargetRemove={removeGameHistoryPartTarget}
                            onGameHistoryPartRoleChange={updateSelectedGameHistoryPart}
                            onGameHistoryPartRemove={removeSelectedGameHistoryPart}
                            onChange={(patch) => {
                                if (selectedLayer) updateLayer(selectedLayer.id, patch);
                            }}
                        />
                    ) : (
                        <AnimationPanel
                            layer={selectedLayer}
                            trackKey={animationTrack}
                            onTrackKeyChange={setAnimationTrack}
                            onPreview={() => setAnimationNonce((value) => value + 1)}
                            onChange={(patch) => {
                                if (selectedLayer) updateLayer(selectedLayer.id, patch);
                            }}
                        />
                    )}
                </Box>
            </Box>
            <Box className="designer-statusbar">
                <span>{message}</span>
                <span>
                    {project.canvas.width} × {project.canvas.height} / {project.layers.length} layers
                </span>
            </Box>
            <Dialog
                open={projectDialogOpen}
                onClose={() => setProjectDialogOpen(false)}
                fullWidth
                maxWidth="sm"
            >
                <DialogTitle>カスタムデザインを開く</DialogTitle>
                <DialogContent dividers>
                    <Stack gap={1}>
                        {projectChoices.map((summary) => (
                            <Stack
                                key={summary.slug}
                                direction="row"
                                gap={1}
                                alignItems="center"
                            >
                                <Button
                                    variant="outlined"
                                    onClick={() => selectProject(summary)}
                                    sx={{justifyContent: "flex-start", flex: 1}}
                                >
                                    {summary.name}
                                    <Typography
                                        component="span"
                                        variant="caption"
                                        color="text.secondary"
                                        sx={{ml: 1}}
                                    >
                                        layout/custom/{summary.slug}
                                    </Typography>
                                </Button>
                                <Tooltip title="デザインを削除">
                                    <IconButton
                                        color="error"
                                        aria-label={`${summary.name}を削除`}
                                        onClick={() => deleteProject(summary)}
                                    >
                                        <Delete />
                                    </IconButton>
                                </Tooltip>
                            </Stack>
                        ))}
                        {!projectChoices.length && (
                            <Typography color="text.secondary">
                                保存済みのデザインはありません。
                            </Typography>
                        )}
                    </Stack>
                </DialogContent>
            </Dialog>
        </Box>
    );
}
