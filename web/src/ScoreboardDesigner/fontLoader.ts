import {ScoreboardProject} from "./types";

const loadedFonts = new Map<string, HTMLLinkElement>();
const bundledFonts = new Set(["LINE Seed JP"]);

function normalizeFamily(value: unknown): string {
    return typeof value === "string"
        ? value.trim().replace(/^['"]|['"]$/g, "")
        : "";
}

function normalizeWeight(value: unknown): number {
    const weight = Number(value);
    if (!Number.isFinite(weight)) return 400;
    return Math.max(100, Math.min(900, Math.round(weight / 100) * 100));
}

function googleFontUrl(family: string, weights: number[]): string {
    const axisValues = [...new Set(weights)]
        .sort((left, right) => left - right)
        .flatMap((weight) => [`0,${weight}`, `1,${weight}`])
        .join(";");
    const encodedFamily = encodeURIComponent(family).replace(/%20/g, "+");
    return `https://fonts.googleapis.com/css2?family=${encodedFamily}` +
        `:ital,wght@${axisValues}&display=swap`;
}

export function ensureProjectGoogleFonts(project: ScoreboardProject): void {
    if (typeof document === "undefined") return;

    const requests = new Map<string, Set<number>>();
    project.layers.forEach((layer) => {
        if (layer.type !== "data-text") return;
        const family = normalizeFamily(layer.typography.googleFontFamily);
        if (!family || bundledFonts.has(family)) return;
        const weights = requests.get(family) ?? new Set<number>();
        weights.add(normalizeWeight(layer.typography.fontWeight));
        requests.set(family, weights);
    });

    requests.forEach((weights, family) => {
        const weightKey = [...weights].sort((left, right) => left - right).join(",");
        const key = `${family}|${weightKey}`;
        if (loadedFonts.has(key)) return;
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = googleFontUrl(family, [...weights]);
        link.dataset.autonaGoogleFont = family;
        document.head.appendChild(link);
        loadedFonts.set(key, link);
    });
}
