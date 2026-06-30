import React from "react";
import {Box} from "@mui/material";
import {BASE_URL} from "../env";
import {playLayerAnimation} from "./animation";
import {resolveBinding} from "./bindings";
import {
    AssetMap,
    DataTextLayer,
    DesignerLayer,
    GameHistoryLayer,
    ScoreboardProject,
} from "./types";

interface Props {
    project: ScoreboardProject;
    assets: AssetMap;
    previewState: any;
    selectedIds: string[];
    primarySelectedId: string | null;
    animationNonce: number;
    onSelectionChange: (ids: string[], primaryId: string | null) => void;
    onLayerChange: (id: string, patch: Partial<DesignerLayer>) => void;
    onInteractionStart?: () => void;
    onInteractionEnd?: () => void;
}

type InteractionMode = "move" | "resize" | "rotate";

interface Rect {
    x: number;
    y: number;
    width: number;
    height: number;
}

interface Point {
    x: number;
    y: number;
}

const MIN_SCALE = 0.1;
const MAX_SCALE = 4;
const SNAP_THRESHOLD = 6;

function AutoFitText({layer, value}: {layer: DataTextLayer; value: string}) {
    const containerRef = React.useRef<HTMLDivElement>(null);
    const contentRef = React.useRef<HTMLDivElement>(null);
    const [scaleX, setScaleX] = React.useState(1);

    React.useLayoutEffect(() => {
        const fit = () => {
            const container = containerRef.current;
            const content = contentRef.current;
            if (!container || !content) return;
            const available = Math.max(1, container.clientWidth);
            setScaleX(Math.min(1, available / Math.max(1, content.scrollWidth)));
        };
        fit();
        const observer = new ResizeObserver(fit);
        if (containerRef.current) observer.observe(containerRef.current);
        return () => observer.disconnect();
    }, [value, layer.width, layer.typography]);

    return (
        <div
            ref={containerRef}
            className="designer-data-text"
            style={{
                justifyContent: layer.typography.align === "left"
                    ? "flex-start"
                    : layer.typography.align === "right" ? "flex-end" : "center",
            }}
        >
            <div
                ref={contentRef}
                style={{
                    color: layer.typography.color,
                    fontFamily: layer.typography.fontFamily,
                    fontSize: layer.typography.fontSize,
                    fontWeight: layer.typography.fontWeight,
                    fontStyle: layer.typography.italic ? "italic" : "normal",
                    letterSpacing: layer.typography.letterSpacing,
                    transform: `scaleX(${scaleX})`,
                    transformOrigin: layer.typography.align,
                }}
            >
                {value}
            </div>
        </div>
    );
}

function GameHistory({
    layer,
    state,
}: {
    layer: GameHistoryLayer;
    state: any;
}) {
    const bestOf = Math.max(0, Number(resolveBinding("games", state)) || 0);
    const p1 = Math.max(0, Number(resolveBinding("p1.score", state)) || 0);
    const p2 = Math.max(0, Number(resolveBinding("p2.score", state)) || 0);
    return (
        <div className="designer-games" style={{gap: layer.gap}}>
            {Array.from({length: bestOf}, (_, index) => {
                const color = index < p1
                    ? layer.p1Color
                    : index < p1 + p2 ? layer.p2Color : layer.neutralColor;
                return (
                    <div
                        key={index}
                        style={{
                            flex: 1,
                            height: "100%",
                            background: color,
                            border: `${layer.borderWidth}px solid ${layer.borderColor}`,
                            borderRadius: layer.radius,
                            boxSizing: "border-box",
                        }}
                    />
                );
            })}
        </div>
    );
}

function previewImageSource(value: string): string {
    if (value.startsWith("../logo.png")) {
        return `${BASE_URL}/layout/logo.png${value.includes("?") ? value.slice(value.indexOf("?")) : ""}`;
    }
    return value;
}

function LayerContent({
    layer,
    assets,
    state,
}: {
    layer: DesignerLayer;
    assets: AssetMap;
    state: any;
}) {
    if (layer.type === "image") {
        const source = assets[layer.asset]?.previewUrl;
        return source
            ? <img src={source} alt="" style={{objectFit: layer.objectFit}} />
            : <div className="designer-missing-asset">画像なし<br />{layer.asset}</div>;
    }
    if (layer.type === "data-text") {
        return <AutoFitText layer={layer} value={String(resolveBinding(layer.binding, state))} />;
    }
    if (layer.type === "data-image") {
        const source = previewImageSource(String(resolveBinding(layer.binding, state)));
        return source
            ? <img src={source} alt="" style={{objectFit: layer.objectFit}} />
            : <div className="designer-empty-data">画像データなし</div>;
    }
    return <GameHistory layer={layer} state={state} />;
}

function CanvasLayer({
    layer,
    assets,
    state,
    selected,
    primary,
    animationNonce,
    onPointerStart,
    onPointerSelect,
}: {
    layer: DesignerLayer;
    assets: AssetMap;
    state: any;
    selected: boolean;
    primary: boolean;
    animationNonce: number;
    onPointerStart: (
        event: React.PointerEvent,
        layer: DesignerLayer,
        mode: InteractionMode,
    ) => void;
    onPointerSelect: (event: React.PointerEvent, layer: DesignerLayer) => void;
}) {
    const animatedRef = React.useRef<HTMLDivElement>(null);
    React.useEffect(() => {
        if (animatedRef.current && layer.visible) {
            playLayerAnimation(animatedRef.current, layer.animation, layer.opacity);
        }
    }, [animationNonce]); // eslint-disable-line react-hooks/exhaustive-deps

    if (!layer.visible) return null;
    return (
        <div
            className={[
                "designer-canvas-layer",
                selected ? "selected" : "",
                selected ? `selected-${layer.type}` : "",
            ].filter(Boolean).join(" ")}
            style={{
                left: layer.x,
                top: layer.y,
                width: layer.width,
                height: layer.height,
                zIndex: layer.zIndex,
                transform: `rotate(${layer.rotation}deg)`,
            }}
            onPointerDown={(event) => {
                onPointerSelect(event, layer);
                onPointerStart(event, layer, "move");
            }}
            data-layer-id={layer.id}
        >
            <div
                ref={animatedRef}
                className="designer-layer-content"
                style={{opacity: layer.opacity}}
            >
                <LayerContent layer={layer} assets={assets} state={state} />
            </div>
            {primary && (
                <>
                    <button
                        type="button"
                        className="designer-handle designer-resize-handle"
                        aria-label="サイズ変更"
                        onPointerDown={(event) => onPointerStart(event, layer, "resize")}
                    />
                    <button
                        type="button"
                        className="designer-handle designer-rotate-handle"
                        aria-label="回転"
                        onPointerDown={(event) => onPointerStart(event, layer, "rotate")}
                    />
                </>
            )}
        </div>
    );
}

function clamp(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
}

function normalizeRect(start: Point, end: Point): Rect {
    return {
        x: Math.min(start.x, end.x),
        y: Math.min(start.y, end.y),
        width: Math.abs(end.x - start.x),
        height: Math.abs(end.y - start.y),
    };
}

function intersects(a: Rect, b: Rect): boolean {
    return a.x <= b.x + b.width &&
        a.x + a.width >= b.x &&
        a.y <= b.y + b.height &&
        a.y + a.height >= b.y;
}

function containsPoint(layer: DesignerLayer, point: Point): boolean {
    return point.x >= layer.x &&
        point.x <= layer.x + layer.width &&
        point.y >= layer.y &&
        point.y <= layer.y + layer.height;
}

function snapDelta(
    active: DesignerLayer,
    dx: number,
    dy: number,
    layers: DesignerLayer[],
    selectedIds: string[],
    canvas: ScoreboardProject["canvas"],
    threshold: number,
    axes: {x: boolean; y: boolean},
): {dx: number; dy: number} {
    const selected = new Set(selectedIds);
    const xTargets = [0, canvas.width / 2, canvas.width];
    const yTargets = [0, canvas.height / 2, canvas.height];
    layers.forEach((layer) => {
        if (!layer.visible || selected.has(layer.id)) return;
        xTargets.push(layer.x, layer.x + layer.width / 2, layer.x + layer.width);
        yTargets.push(layer.y, layer.y + layer.height / 2, layer.y + layer.height);
    });

    const movedX = active.x + dx;
    const movedY = active.y + dy;
    const xPoints = [movedX, movedX + active.width / 2, movedX + active.width];
    const yPoints = [movedY, movedY + active.height / 2, movedY + active.height];
    let bestX = 0;
    let bestXDistance = threshold + 1;
    let bestY = 0;
    let bestYDistance = threshold + 1;

    xPoints.forEach((point) => {
        xTargets.forEach((target) => {
            const distance = Math.abs(target - point);
            if (distance < bestXDistance) {
                bestXDistance = distance;
                bestX = target - point;
            }
        });
    });
    yPoints.forEach((point) => {
        yTargets.forEach((target) => {
            const distance = Math.abs(target - point);
            if (distance < bestYDistance) {
                bestYDistance = distance;
                bestY = target - point;
            }
        });
    });

    return {
        dx: dx + (axes.x && bestXDistance <= threshold ? bestX : 0),
        dy: dy + (axes.y && bestYDistance <= threshold ? bestY : 0),
    };
}

function lockDeltaToAxis(dx: number, dy: number): {dx: number; dy: number; axes: {x: boolean; y: boolean}} {
    if (Math.abs(dx) >= Math.abs(dy)) {
        return {dx, dy: 0, axes: {x: true, y: false}};
    }
    return {dx: 0, dy, axes: {x: false, y: true}};
}

export default function DesignerCanvas({
    project,
    assets,
    previewState,
    selectedIds,
    primarySelectedId,
    animationNonce,
    onSelectionChange,
    onLayerChange,
    onInteractionStart,
    onInteractionEnd,
}: Props) {
    const viewportRef = React.useRef<HTMLDivElement>(null);
    const [scale, setScale] = React.useState(0.5);
    const [pan, setPan] = React.useState({x: 0, y: 0});
    const [selectionRect, setSelectionRect] = React.useState<Rect | null>(null);
    const manualZoomRef = React.useRef(false);
    const spacePressedRef = React.useRef(false);
    const selectedIdsRef = React.useRef(selectedIds);
    const projectRef = React.useRef(project);

    React.useEffect(() => {
        selectedIdsRef.current = selectedIds;
    }, [selectedIds]);

    React.useEffect(() => {
        projectRef.current = project;
    }, [project]);

    React.useLayoutEffect(() => {
        const resize = () => {
            const viewport = viewportRef.current;
            if (!viewport || manualZoomRef.current) return;
            const next = Math.min(
                (viewport.clientWidth - 32) / project.canvas.width,
                (viewport.clientHeight - 32) / project.canvas.height,
            );
            setScale(Math.max(MIN_SCALE, next));
            setPan({x: 0, y: 0});
        };
        resize();
        const observer = new ResizeObserver(resize);
        if (viewportRef.current) observer.observe(viewportRef.current);
        return () => observer.disconnect();
    }, [project.canvas.width, project.canvas.height]);

    React.useEffect(() => {
        const keyDown = (event: KeyboardEvent) => {
            if (event.code === "Space") {
                spacePressedRef.current = true;
                return;
            }
            const directions: Record<string, Point> = {
                ArrowLeft: {x: -1, y: 0},
                ArrowRight: {x: 1, y: 0},
                ArrowUp: {x: 0, y: -1},
                ArrowDown: {x: 0, y: 1},
            };
            const direction = directions[event.key];
            if (!direction || !selectedIdsRef.current.length) return;
            const target = event.target as HTMLElement | null;
            if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
            event.preventDefault();
            const step = event.shiftKey ? 10 : 1;
            selectedIdsRef.current.forEach((id) => {
                const layer = projectRef.current.layers.find((item) => item.id === id);
                if (!layer) return;
                onLayerChange(id, {
                    x: layer.x + direction.x * step,
                    y: layer.y + direction.y * step,
                });
            });
        };
        const keyUp = (event: KeyboardEvent) => {
            if (event.code === "Space") spacePressedRef.current = false;
        };
        window.addEventListener("keydown", keyDown);
        window.addEventListener("keyup", keyUp);
        return () => {
            window.removeEventListener("keydown", keyDown);
            window.removeEventListener("keyup", keyUp);
        };
    }, [onLayerChange]);

    const canvasPoint = React.useCallback((clientX: number, clientY: number): Point => {
        const viewport = viewportRef.current?.getBoundingClientRect();
        if (!viewport) return {x: 0, y: 0};
        const canvasLeft = viewport.left +
            (viewport.width - project.canvas.width * scale) / 2 + pan.x;
        const canvasTop = viewport.top +
            (viewport.height - project.canvas.height * scale) / 2 + pan.y;
        return {
            x: (clientX - canvasLeft) / scale,
            y: (clientY - canvasTop) / scale,
        };
    }, [pan.x, pan.y, project.canvas.height, project.canvas.width, scale]);

    const handlePointerSelect = React.useCallback((
        event: React.PointerEvent,
        layer: DesignerLayer,
    ) => {
        if (event.ctrlKey || event.metaKey) {
            const exists = selectedIds.includes(layer.id);
            const next = exists
                ? selectedIds.filter((id) => id !== layer.id)
                : [...selectedIds, layer.id];
            onSelectionChange(next, exists ? next[next.length - 1] ?? null : layer.id);
            return;
        }
        if (event.shiftKey) {
            const next = selectedIds.includes(layer.id)
                ? selectedIds
                : [...selectedIds, layer.id];
            onSelectionChange(next, layer.id);
            return;
        }
        if (!selectedIds.includes(layer.id)) {
            onSelectionChange([layer.id], layer.id);
        } else {
            onSelectionChange(selectedIds, layer.id);
        }
    }, [onSelectionChange, selectedIds]);

    const startInteraction = React.useCallback((
        event: React.PointerEvent,
        layer: DesignerLayer,
        mode: InteractionMode,
    ) => {
        event.preventDefault();
        event.stopPropagation();
        viewportRef.current?.focus();
        onInteractionStart?.();
        const startX = event.clientX;
        const startY = event.clientY;
        const groupIds = layer.groupId
            ? projectRef.current.layers
                .filter((item) => item.groupId === layer.groupId)
                .map((item) => item.id)
            : [layer.id];
        const activeIds = mode === "move" && selectedIdsRef.current.includes(layer.id)
            ? selectedIdsRef.current
            : groupIds;
        const originalLayers = new Map(
            projectRef.current.layers
                .filter((item) => activeIds.includes(item.id))
                .map((item) => [item.id, {...item}]),
        );
        const original = originalLayers.get(layer.id) ?? layer;
        const centerX = original.x + original.width / 2;
        const centerY = original.y + original.height / 2;

        const move = (moveEvent: PointerEvent) => {
            const rawDx = (moveEvent.clientX - startX) / scale;
            const rawDy = (moveEvent.clientY - startY) / scale;
            if (mode === "move") {
                const constrained = moveEvent.shiftKey
                    ? lockDeltaToAxis(rawDx, rawDy)
                    : {dx: rawDx, dy: rawDy, axes: {x: true, y: true}};
                const snapped = moveEvent.altKey
                    ? {dx: constrained.dx, dy: constrained.dy}
                    : snapDelta(
                        original,
                        constrained.dx,
                        constrained.dy,
                        projectRef.current.layers,
                        activeIds,
                        projectRef.current.canvas,
                        SNAP_THRESHOLD / scale,
                        constrained.axes,
                    );
                activeIds.forEach((id) => {
                    const base = originalLayers.get(id);
                    if (!base) return;
                    onLayerChange(id, {
                        x: Math.round(base.x + snapped.dx),
                        y: Math.round(base.y + snapped.dy),
                    });
                });
            } else if (mode === "resize") {
                onLayerChange(layer.id, {
                    width: Math.max(10, Math.round(original.width + rawDx)),
                    height: Math.max(10, Math.round(original.height + rawDy)),
                });
            } else {
                const pointer = canvasPoint(moveEvent.clientX, moveEvent.clientY);
                const degrees = Math.atan2(pointer.y - centerY, pointer.x - centerX) *
                    180 / Math.PI + 90;
                onLayerChange(layer.id, {rotation: Math.round(degrees)});
            }
        };
        const up = () => {
            onInteractionEnd?.();
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }, [canvasPoint, onInteractionEnd, onInteractionStart, onLayerChange, scale]);

    const startCanvasPointer = React.useCallback((event: React.PointerEvent) => {
        if ((event.target as HTMLElement).closest(".designer-canvas-layer")) return;
        viewportRef.current?.focus();
        event.preventDefault();
        if (spacePressedRef.current || event.button === 1) {
            const startX = event.clientX;
            const startY = event.clientY;
            const originalPan = {...pan};
            const move = (moveEvent: PointerEvent) => {
                setPan({
                    x: originalPan.x + moveEvent.clientX - startX,
                    y: originalPan.y + moveEvent.clientY - startY,
                });
            };
            const up = () => {
                window.removeEventListener("pointermove", move);
                window.removeEventListener("pointerup", up);
            };
            window.addEventListener("pointermove", move);
            window.addEventListener("pointerup", up);
            return;
        }

        const start = canvasPoint(event.clientX, event.clientY);
        setSelectionRect({...start, width: 0, height: 0});
        const move = (moveEvent: PointerEvent) => {
            setSelectionRect(normalizeRect(start, canvasPoint(moveEvent.clientX, moveEvent.clientY)));
        };
        const up = (upEvent: PointerEvent) => {
            const rect = normalizeRect(start, canvasPoint(upEvent.clientX, upEvent.clientY));
            setSelectionRect(null);
            if (rect.width < 3 && rect.height < 3) {
                onSelectionChange([], null);
            } else {
                const selected = projectRef.current.layers
                    .filter((item) => item.visible)
                    .filter((item) => intersects(rect, {
                        x: item.x,
                        y: item.y,
                        width: item.width,
                        height: item.height,
                    }))
                    .sort((a, b) => b.zIndex - a.zIndex)
                    .map((item) => item.id);
                onSelectionChange(selected, selected[0] ?? null);
            }
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }, [canvasPoint, onSelectionChange, pan]);

    const handleDoubleClick = React.useCallback((event: React.MouseEvent) => {
        const point = canvasPoint(event.clientX, event.clientY);
        const candidates = project.layers
            .filter((layer) => layer.visible && containsPoint(layer, point))
            .sort((a, b) => b.zIndex - a.zIndex);
        if (!candidates.length) return;
        const currentIndex = candidates.findIndex((layer) => layer.id === primarySelectedId);
        const next = candidates[(currentIndex + 1) % candidates.length];
        onSelectionChange([next.id], next.id);
    }, [canvasPoint, onSelectionChange, primarySelectedId, project.layers]);

    const handleWheel = React.useCallback((event: React.WheelEvent) => {
        event.preventDefault();
        if (event.ctrlKey || event.metaKey) {
            manualZoomRef.current = true;
            const nextScale = clamp(
                scale * (event.deltaY < 0 ? 1.1 : 0.9),
                MIN_SCALE,
                MAX_SCALE,
            );
            setScale(nextScale);
        } else {
            setPan((current) => ({
                x: current.x - event.deltaX,
                y: current.y - event.deltaY,
            }));
        }
    }, [scale]);

    return (
        <Box
            ref={viewportRef}
            className="designer-canvas-viewport"
            tabIndex={0}
            onPointerDown={startCanvasPointer}
            onDoubleClick={handleDoubleClick}
            onWheel={handleWheel}
        >
            <div
                className="designer-canvas-shell"
                style={{
                    width: project.canvas.width * scale,
                    height: project.canvas.height * scale,
                    transform: `translate(${pan.x}px, ${pan.y}px)`,
                }}
            >
                <div
                    className="designer-canvas"
                    style={{
                        width: project.canvas.width,
                        height: project.canvas.height,
                        backgroundColor: project.canvas.backgroundColor,
                        transform: `scale(${scale})`,
                    }}
                >
                    {[...project.layers]
                        .sort((a, b) => a.zIndex - b.zIndex)
                        .map((layer) => (
                            <CanvasLayer
                                key={layer.id}
                                layer={layer}
                                assets={assets}
                                state={previewState}
                                selected={selectedIds.includes(layer.id)}
                                primary={primarySelectedId === layer.id}
                                animationNonce={animationNonce}
                                onPointerStart={startInteraction}
                                onPointerSelect={handlePointerSelect}
                            />
                        ))}
                    {selectionRect && (
                        <div
                            className="designer-selection-rect"
                            style={{
                                left: selectionRect.x,
                                top: selectionRect.y,
                                width: selectionRect.width,
                                height: selectionRect.height,
                            }}
                        />
                    )}
                </div>
            </div>
        </Box>
    );
}
