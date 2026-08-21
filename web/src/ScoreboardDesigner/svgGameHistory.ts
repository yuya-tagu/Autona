import {resolveBinding} from "./bindings";
import {
    GameHistoryImagePart,
    GameHistoryImageStyle,
} from "./types";

export type GameHistoryImageStatus = "p1" | "p2" | "neutral";
const DRAWABLE_SELECTOR = "path,circle,ellipse,line,polygon,polyline,rect,use";

function scoreValue(binding: "p1.score" | "p2.score", state: any): number {
    return Math.max(0, Number(resolveBinding(binding, state)) || 0);
}

function bestOfValue(state: any): number {
    return Math.max(0, Number(resolveBinding("games", state)) || 0);
}

export function gameHistoryImageStatus(
    style: GameHistoryImageStyle,
    state: any,
): GameHistoryImageStatus {
    const p1 = scoreValue("p1.score", state);
    const p2 = scoreValue("p2.score", state);
    if (style.slotIndex < p1) return "p1";
    if (style.slotIndex < p1 + p2) return "p2";
    return "neutral";
}

export function gameHistoryImagePartStatus(
    part: GameHistoryImagePart,
    state: any,
): GameHistoryImageStatus {
    const p1 = scoreValue("p1.score", state);
    const p2 = scoreValue("p2.score", state);
    if (part.role === "winner") {
        const target = Math.ceil(bestOfValue(state) / 2);
        const p1Won = target > 0 && p1 >= target;
        const p2Won = target > 0 && p2 >= target;
        if (p1Won === p2Won) return "neutral";
        return p1Won ? "p1" : "p2";
    }
    const setIndex = Number(part.setIndex);
    if (!Number.isInteger(setIndex) || setIndex < 0) return "neutral";
    if (part.role === "p1-set") return setIndex < p1 ? "p1" : "neutral";
    if (part.role === "p2-set") return setIndex < p2 ? "p2" : "neutral";
    if (setIndex < p1) return "p1";
    if (setIndex < p1 + p2) return "p2";
    return "neutral";
}

/** Resolves multiple assignments that intentionally reference one SVG path. */
export function gameHistoryImagePartsStatus(
    parts: GameHistoryImagePart[],
    state: any,
): GameHistoryImageStatus {
    const statuses = parts.map((part) => gameHistoryImagePartStatus(part, state));
    if (statuses.includes("p1")) return "p1";
    if (statuses.includes("p2")) return "p2";
    return "neutral";
}

export function isGameHistoryImagePartHidden(
    part: GameHistoryImagePart,
    state: any,
): boolean {
    // Explicit P1/P2 lamps are part of the supplied artwork and must remain
    // visible even when best_of is temporarily unset or smaller than five.
    // Only the legacy global-order slots are truncated by best_of.
    return part.role === "set" &&
        Number(part.setIndex) >= bestOfValue(state);
}

function safeCssColor(value: string, fallback: string): string {
    const candidate = String(value || "").trim();
    return /^[#(),.%\w\s-]+$/.test(candidate) ? candidate : fallback;
}

function parseAndSanitizeSvg(source: string): Element | null {
    if (typeof DOMParser === "undefined") return null;
    const document = new DOMParser().parseFromString(source, "image/svg+xml");
    const root = document.documentElement;
    if (!root || root.nodeName.toLowerCase() !== "svg") return null;

    root.querySelectorAll("script,foreignObject").forEach((element) => element.remove());
    root.querySelectorAll("*").forEach((element) => {
        Array.from(element.attributes).forEach((attribute) => {
            const name = attribute.name.toLowerCase();
            const value = attribute.value.trim().toLowerCase();
            if (name.startsWith("on") ||
                ((name === "href" || name === "src") &&
                    /^(https?:|\/\/|javascript:)/.test(value))) {
                element.removeAttribute(attribute.name);
            }
        });
    });
    return root;
}

export function svgElementPath(element: Element, root: Element): number[] | null {
    const path: number[] = [];
    let current: Element | null = element;
    while (current && current !== root) {
        const parent = current.parentElement;
        if (!parent) return null;
        const index = Array.from(parent.children).indexOf(current);
        if (index < 0) return null;
        path.unshift(index);
        current = parent;
    }
    return current === root ? path : null;
}

export function findSvgElementByPath(root: Element, path: number[]): Element | null {
    let current: Element = root;
    for (const index of path) {
        const child = current.children.item(index);
        if (!child) return null;
        current = child;
    }
    return current;
}

function pathKey(path: number[]): string {
    return path.join(".");
}

function colorizeSvgElement(
    element: Element,
    status: GameHistoryImageStatus,
    style: GameHistoryImageStyle,
): void {
    const activeColor = status === "p1"
        ? style.p1Color
        : status === "p2" ? style.p2Color : style.neutralColor;
    const color = safeCssColor(activeColor, "#ffffff");
    const drawableElements = element.matches(DRAWABLE_SELECTOR)
        ? [element, ...Array.from(element.querySelectorAll(DRAWABLE_SELECTOR))]
        : Array.from(element.querySelectorAll(DRAWABLE_SELECTOR));
    drawableElements.forEach((drawable) => {
        const svgDrawable = drawable as SVGElement;
        if (status === "neutral") {
            svgDrawable.style.setProperty("fill", "none", "important");
            svgDrawable.style.setProperty("stroke", color, "important");
            svgDrawable.style.setProperty(
                "stroke-width", `${Math.max(0, style.strokeWidth)}px`, "important",
            );
            svgDrawable.style.setProperty("stroke-linejoin", "round", "important");
            svgDrawable.style.setProperty("stroke-linecap", "round", "important");
        } else {
            svgDrawable.style.setProperty("fill", color, "important");
            svgDrawable.style.setProperty("stroke", "none", "important");
        }
    });
}

/**
 * Rewrites only drawable SVG elements so an imported SVG can be shown as an
 * outline or a filled game-history marker without changing the source asset.
 */
export function colorizeGameHistorySvg(
    source: string,
    status: GameHistoryImageStatus,
    style: GameHistoryImageStyle,
): string | null {
    if (typeof XMLSerializer === "undefined") {
        return null;
    }
    const root = parseAndSanitizeSvg(source);
    if (!root) return null;
    colorizeSvgElement(root, status, style);
    return new XMLSerializer().serializeToString(root);
}

/** Colorizes only configured SVG elements; unconfigured elements remain intact. */
export function colorizeGameHistorySvgParts(
    source: string,
    style: GameHistoryImageStyle,
    state: any,
    selectedPath: number[] | null = null,
): string | null {
    if (typeof XMLSerializer === "undefined") return null;
    const root = parseAndSanitizeSvg(source);
    if (!root) return null;

    if (selectedPath) {
        const selected = findSvgElementByPath(root, selectedPath);
        selected?.setAttribute("data-autona-part-selected", "true");
    }
    const partsByPath = new Map<string, GameHistoryImagePart[]>();
    (style.parts ?? []).forEach((part) => {
        const key = pathKey(part.path);
        const parts = partsByPath.get(key) ?? [];
        parts.push(part);
        partsByPath.set(key, parts);
    });
    partsByPath.forEach((parts) => {
        const part = parts[0];
        const element = findSvgElementByPath(root, part.path);
        if (!element) return;
        element.setAttribute("data-autona-part-path", pathKey(part.path));
        if (parts.some((candidate) => isGameHistoryImagePartHidden(candidate, state))) {
            element.setAttribute("visibility", "hidden");
            return;
        }
        element.removeAttribute("visibility");
        colorizeSvgElement(element, gameHistoryImagePartsStatus(parts, state), style);
    });
    return new XMLSerializer().serializeToString(root);
}
