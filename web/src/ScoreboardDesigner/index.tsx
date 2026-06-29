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
    Close,
    ContentCopy,
    Delete,
    DragIndicator,
    FolderOpen,
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
import {resolveBinding, SAMPLE_STATE} from "./bindings";
import DesignerCanvas from "./DesignerCanvas";
import LayerInspector from "./LayerInspector";
import {
    AssetMap,
    BindingKey,
    DesignerLayer,
    ImageLayer,
    ScoreboardProject,
} from "./types";
import {
    BINDING_LABELS,
    cloneLayer,
    createBindingLayer,
    createEmptyProject,
    createId,
    validateProject,
} from "./model";
import {
    dataUrlToAsset,
    deleteDesktopProject,
    DesignerProjectSummary,
    isDesktopDesigner,
    listDesktopProjects,
    loadDesktopProject,
    makeUniqueAssetPath,
    revokeAssetUrls,
    saveDesktopProject,
} from "./desktopApi";
import {BINDINGS} from "./types";
import "./styles.css";

function maxZ(project: ScoreboardProject): number {
    return project.layers.reduce((max, layer) => Math.max(max, layer.zIndex), 0);
}

function orderedPanelLayers(project: ScoreboardProject): DesignerLayer[] {
    return [...project.layers].sort((a, b) => b.zIndex - a.zIndex);
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
    if (layer.type !== "data-text" || !AUTO_LAYER_NAME_BINDINGS.has(layer.binding)) {
        return null;
    }
    const value = String(resolveBinding(layer.binding, state)).trim();
    return value || null;
}

function defaultTemplateProject(): ScoreboardProject {
    const project = createEmptyProject("標準スコアボード");
    const specs: Array<{
        binding: BindingKey;
        x: number;
        y: number;
        width: number;
        height: number;
    }> = [
        {binding: "tournament.name", x: 560, y: 54, width: 800, height: 64},
        {binding: "p1.name", x: 170, y: 872, width: 520, height: 72},
        {binding: "p2.name", x: 1230, y: 872, width: 520, height: 72},
        {binding: "p1.score", x: 735, y: 842, width: 120, height: 110},
        {binding: "p2.score", x: 1065, y: 842, width: 120, height: 110},
        {binding: "games", x: 790, y: 966, width: 340, height: 42},
    ];
    project.layers = specs.map((spec, index) => ({
        ...createBindingLayer(spec.binding, index + 1),
        x: spec.x,
        y: spec.y,
        width: spec.width,
        height: spec.height,
    } as DesignerLayer));
    return project;
}

export default function ScoreboardDesigner() {
    const liveState = useSelector((state: ReduxState) => state.tshState.tshState);
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
    const [bindingToAdd, setBindingToAdd] = React.useState<BindingKey>("p1.name");
    const [sampleMode, setSampleMode] = React.useState(false);
    const [animationNonce, setAnimationNonce] = React.useState(0);
    const [message, setMessage] = React.useState("未保存");
    const [projectChoices, setProjectChoices] =
        React.useState<DesignerProjectSummary[]>([]);
    const [projectDialogOpen, setProjectDialogOpen] = React.useState(false);
    const [currentSlug, setCurrentSlug] = React.useState<string | null>(null);
    const [editingLayerId, setEditingLayerId] = React.useState<string | null>(null);
    const [editingLayerName, setEditingLayerName] = React.useState("");
    const [layerDrag, setLayerDrag] = React.useState<LayerDragState | null>(null);
    const imageInputRef = React.useRef<HTMLInputElement>(null);
    const figmaInputRef = React.useRef<HTMLInputElement>(null);
    const layerListRef = React.useRef<HTMLDivElement>(null);
    const displayedLayersRef = React.useRef<DesignerLayer[]>([]);
    const assetsRef = React.useRef(assets);
    const autoLayerNamesRef = React.useRef<Record<string, string>>({});
    const canvasInteractionRef = React.useRef(false);
    const canvasInteractionCapturedRef = React.useRef(false);
    const layerRowRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});
    const previousLayerRowTopsRef = React.useRef<Map<string, number>>(new Map());
    const desktopMode = React.useMemo(isDesktopDesigner, []);

    React.useEffect(() => {
        document.title = "Autona Scoreboard Designer";
        if (desktopMode) websocketInit();
    }, [desktopMode]);

    React.useEffect(() => {
        assetsRef.current = assets;
    }, [assets]);

    React.useEffect(
        () => () => revokeAssetUrls(assetsRef.current),
        [],
    );

    const orderedLayers = React.useMemo(() => orderedPanelLayers(project), [project]);
    const displayedLayers = React.useMemo(() => {
        if (!layerDrag) return orderedLayers;
        const byId = new Map(orderedLayers.map((layer) => [layer.id, layer]));
        return layerDrag.orderIds
            .map((id) => byId.get(id))
            .filter(Boolean) as DesignerLayer[];
    }, [layerDrag, orderedLayers]);
    const displayedLayerOrderKey = React.useMemo(
        () => displayedLayers.map((layer) => layer.id).join("|"),
        [displayedLayers],
    );
    displayedLayersRef.current = displayedLayers;
    const floatingLayer = layerDrag
        ? orderedLayers.find((layer) => layer.id === layerDrag.sourceId) ?? null
        : null;
    const selectedLayer = project.layers.find((layer) => layer.id === primarySelectedId) ?? null;
    const selectedLayers = selectedIds
        .map((id) => project.layers.find((layer) => layer.id === id))
        .filter(Boolean) as DesignerLayer[];
    const bindingCounts = React.useMemo(() => {
        const counts = new Map<BindingKey, number>();
        project.layers.forEach((layer) => {
            if ("binding" in layer) {
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
        setEditingLayerId(null);
        setEditingLayerName("");
        setMessage("元に戻しました。");
    }, [canUndo]);

    const redoProject = React.useCallback(() => {
        if (!canRedo) return;
        dispatchProject({type: "redo"});
        setLayerDrag(null);
        setEditingLayerId(null);
        setEditingLayerName("");
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
        let zIndex = maxZ(project) + 1;
        let lastAddedId: string | null = null;

        for (const file of Array.from(files)) {
            if (file.type !== "image/png" && !file.name.toLowerCase().endsWith(".png")) {
                setMessage(`${file.name}: PNGのみ追加できます。`);
                continue;
            }
            try {
                const bitmap = await createImageBitmap(file);
                const path = makeUniqueAssetPath(file.name, nextAssets);
                const previewUrl = URL.createObjectURL(file);
                nextAssets[path] = {path, blob: file, previewUrl};
                const layer: ImageLayer = {
                    id: createId(),
                    name: file.name.replace(/\.png$/i, ""),
                    type: "image",
                    asset: path,
                    objectFit: "contain",
                    x: Math.round((1920 - bitmap.width) / 2),
                    y: Math.round((1080 - bitmap.height) / 2),
                    width: bitmap.width,
                    height: bitmap.height,
                    rotation: 0,
                    opacity: 1,
                    zIndex: zIndex++,
                    visible: true,
                    animation: {preset: "fade", durationMs: 450, delayMs: 0},
                };
                bitmap.close();
                nextLayers.push(layer);
                lastAddedId = layer.id;
            } catch {
                setMessage(`${file.name}: 画像を読み込めませんでした。`);
            }
        }
        setAssets(nextAssets);
        applyProjectChange((current) => ({...current, layers: nextLayers}), "PNGレイヤーを追加しました。");
        if (lastAddedId) setSelection([lastAddedId], lastAddedId);
        if (imageInputRef.current) imageInputRef.current.value = "";
    };

    const deleteSelected = () => {
        if (!selectedIds.length) return;
        const ids = new Set(selectedIds);
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.filter((layer) => !ids.has(layer.id)),
        }));
        setSelection([], null);
    };

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
                {id: groupId, name: `グループ ${(current.groups?.length ?? 0) + 1}`},
            ],
            layers: current.layers.map((layer) =>
                selectedIds.includes(layer.id)
                    ? ({...layer, groupId} as DesignerLayer)
                    : layer,
            ),
        }));
    };

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
        const template = defaultTemplateProject();
        setAssets({});
        resetProject(template, "標準テンプレートを読み込みました。");
        setSelection([template.layers[0].id], template.layers[0].id);
        setCurrentSlug(null);
    };

    const importFigmaJson = async (files: FileList | null) => {
        const file = files?.[0];
        if (!file) return;
        try {
            const text = await file.text();
            const parsed = JSON.parse(text);
            const importedProject = validateProject(parsed.project ?? parsed);
            const importedAssets: AssetMap = {};
            for (const [path, dataUrl] of Object.entries(parsed.assets ?? {})) {
                if (typeof dataUrl === "string") {
                    importedAssets[path] = await dataUrlToAsset(path, dataUrl);
                }
            }
            revokeAssetUrls(assets);
            resetProject(importedProject, "Figma JSONを読み込みました。");
            setAssets(importedAssets);
            setSelection(importedProject.layers[0] ? [importedProject.layers[0].id] : [], importedProject.layers[0]?.id ?? null);
            setCurrentSlug(null);
        } catch (error: any) {
            setMessage(error?.message || "Figma JSONを読み込めませんでした。");
        } finally {
            if (figmaInputRef.current) figmaInputRef.current.value = "";
        }
    };

    const reorderSelected = (direction: -1 | 1) => {
        if (!selectedLayer) return;
        const ordered = [...project.layers].sort((a, b) => a.zIndex - b.zIndex);
        const index = ordered.findIndex((layer) => layer.id === selectedLayer.id);
        const targetIndex = index + direction;
        if (targetIndex < 0 || targetIndex >= ordered.length) return;
        const target = ordered[targetIndex];
        const currentZ = selectedLayer.zIndex;
        applyProjectChange((current) => ({
            ...current,
            layers: current.layers.map((layer) => {
                if (layer.id === selectedLayer.id) {
                    return {...layer, zIndex: target.zIndex} as DesignerLayer;
                }
                if (layer.id === target.id) {
                    return {...layer, zIndex: currentZ} as DesignerLayer;
                }
                return layer;
            }),
        }));
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
                    <Typography variant="h6" sx={{mr: 2}}>Scoreboard Designer</Typography>
                    <TextField
                        size="small"
                        label="デザイン名"
                        value={project.name}
                        onChange={(event) => {
                            const name = event.target.value;
                            applyProjectChange((current) => ({...current, name}));
                        }}
                        sx={{width: 240}}
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
                        保存・OBS出力
                    </Button>
                    <Button
                        startIcon={<PlayArrow />}
                        onClick={() => setAnimationNonce((value) => value + 1)}
                    >
                        アニメーション
                    </Button>
                    <Box className="designer-data-toggle">
                        <Typography className="designer-data-toggle-label">
                            {sampleMode ? "サンプルデータ" : "実データ"}
                        </Typography>
                        <Switch
                            size="small"
                            checked={!sampleMode}
                            onChange={(event) => setSampleMode(!event.target.checked)}
                            inputProps={{"aria-label": "実データを使用"}}
                        />
                        <Typography
                            variant="caption"
                            color="text.secondary"
                            className="designer-data-toggle-status"
                        >
                            {!sampleMode && !hasLiveState
                                ? `待機中 (${websocketStatus})`
                                : websocketStatus}
                        </Typography>
                    </Box>
                    <Button
                        startIcon={<Close />}
                        color="inherit"
                        onClick={() => window.close()}
                    >
                        閉じる
                    </Button>
                </Toolbar>
            </AppBar>

            <Box className="designer-workspace">
                <Box component="aside" className="designer-sidebar designer-left-sidebar">
                    <Typography variant="subtitle2">追加</Typography>
                    <input
                        ref={imageInputRef}
                        type="file"
                        accept="image/png,.png"
                        multiple
                        hidden
                        onChange={(event) => addImages(event.target.files)}
                    />
                    <input
                        ref={figmaInputRef}
                        type="file"
                        accept="application/json,.json"
                        hidden
                        onChange={(event) => importFigmaJson(event.target.files)}
                    />
                    <Button
                        fullWidth
                        variant="outlined"
                        startIcon={<AddPhotoAlternate />}
                        onClick={() => imageInputRef.current?.click()}
                    >
                        PNGを追加
                    </Button>
                    <Stack direction="row" gap={1}>
                        <Button fullWidth variant="outlined" onClick={loadDefaultTemplate}>
                            標準テンプレート
                        </Button>
                        <Button fullWidth variant="outlined" onClick={() => figmaInputRef.current?.click()}>
                            Figma JSON
                        </Button>
                    </Stack>
                    <Stack direction="row" gap={1}>
                        <Select
                            size="small"
                            fullWidth
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
                                        disabled={!selectedLayer}
                                        onClick={() => reorderSelected(-1)}
                                    ><ArrowDownward fontSize="small" /></IconButton>
                                </span>
                            </Tooltip>
                            <Tooltip title="前面へ">
                                <span>
                                    <IconButton
                                        size="small"
                                        disabled={!selectedLayer}
                                        onClick={() => reorderSelected(1)}
                                    ><ArrowUpward fontSize="small" /></IconButton>
                                </span>
                            </Tooltip>
                            <Tooltip title="複製">
                                <span>
                                    <IconButton
                                        size="small"
                                        disabled={!selectedLayers.length}
                                        onClick={duplicateSelected}
                                    ><ContentCopy fontSize="small" /></IconButton>
                                </span>
                            </Tooltip>
                            <Tooltip title="削除">
                                <span>
                                    <IconButton
                                        size="small"
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
                        {displayedLayers
                            .map((layer) => (
                                <button
                                    type="button"
                                    key={layer.id}
                                    ref={(element) => {
                                        layerRowRefs.current[layer.id] = element;
                                    }}
                                    data-layer-id={layer.id}
                                    className={`designer-layer-row${
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
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            updateLayer(layer.id, {visible: !layer.visible});
                                        }}
                                    >
                                        {layer.visible
                                            ? <Visibility fontSize="small" />
                                            : <VisibilityOff fontSize="small" />}
                                    </IconButton>
                                </button>
                            ))}
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
                    onSelectionChange={setSelection}
                    onLayerChange={updateLayer}
                    onInteractionStart={beginCanvasInteraction}
                    onInteractionEnd={endCanvasInteraction}
                />

                <Box component="aside" className="designer-sidebar designer-right-sidebar">
                    <Typography variant="subtitle2">プロパティ</Typography>
                    <Divider />
                    <LayerInspector
                        layer={selectedLayer}
                        onChange={(patch) => {
                            if (selectedLayer) updateLayer(selectedLayer.id, patch);
                        }}
                    />
                </Box>
            </Box>
            <Box className="designer-statusbar">
                <span>{message}</span>
                <span>1920 × 1080 / {project.layers.length} layers</span>
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
