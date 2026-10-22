import {
    AssetMap,
    DesignerAsset,
    ScoreboardProject,
} from "./types";
import {validateProject} from "./model";

export interface DesignerProjectSummary {
    slug: string;
    name: string;
}

function token(): string {
    const value = new URLSearchParams(window.location.search).get("token");
    if (!value) {
        throw new Error("デスクトップDesignerの認証情報がありません。");
    }
    return value;
}

export function isDesktopDesigner(): boolean {
    const params = new URLSearchParams(window.location.search);
    return params.get("desktop") === "1" && Boolean(params.get("token"));
}

// Windows device names (the part before the first dot), case-insensitive.
const RESERVED_FIRST_SEGMENT = /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?=\.|$)/i;

export function makeUniqueAssetPath(
    originalName: string,
    assets: AssetMap,
): string {
    const normalized = originalName
        .normalize("NFKC")
        .replace(/[^a-zA-Z0-9._-]+/g, "-")
        .replace(/^-+/, "") || "layer.png";
    const dot = normalized.lastIndexOf(".");
    let base = dot > 0 ? normalized.slice(0, dot) : normalized;
    // Mirror ASSET_PATH_PATTERN in AutonaDesignerService.py: no dot/space/hyphen
    // right before the extension, and no Windows device name before the first dot.
    base = base.replace(/[.\s-]+$/, "") || "layer";
    base = base.replace(RESERVED_FIRST_SEGMENT, "$1-file");
    const extension = dot > 0 ? normalized.slice(dot).toLowerCase() : ".png";
    let candidate = `assets/${base}${extension}`;
    let index = 2;
    while (assets[candidate]) {
        candidate = `assets/${base}-${index}${extension}`;
        index += 1;
    }
    return candidate;
}

export function revokeAssetUrls(assets: AssetMap): void {
    Object.values(assets).forEach((asset) =>
        URL.revokeObjectURL(asset.previewUrl));
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(path, {
        ...init,
        headers: {
            "X-Autona-Designer-Token": token(),
            ...(init?.body && !(init.body instanceof FormData)
                ? {"Content-Type": "application/json"}
                : {}),
            ...init?.headers,
        },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(body.error || `Designer API error: ${response.status}`);
    }
    return body as T;
}

function blobToDataUrl(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("画像を読み込めませんでした。"));
        reader.readAsDataURL(blob);
    });
}

export async function dataUrlToAsset(
    path: string,
    dataUrl: string,
): Promise<DesignerAsset> {
    const response = await fetch(dataUrl);
    const blob = await response.blob();
    return {
        path,
        blob,
        previewUrl: URL.createObjectURL(blob),
    };
}

export async function listDesktopProjects(): Promise<DesignerProjectSummary[]> {
    const result = await apiRequest<{projects: DesignerProjectSummary[]}>(
        "/api/designer/projects",
    );
    return result.projects;
}

export async function saveDesktopProject(
    project: ScoreboardProject,
    assets: AssetMap,
): Promise<{slug: string; path: string}> {
    const validated = validateProject(project);
    const encodedAssets: Record<string, string> = {};
    const assetPaths = validated.assets.map((asset) => asset.path);
    for (const path of assetPaths) {
        if (encodedAssets[path]) continue;
        const asset = assets[path];
        if (!asset) {
            throw new Error(`${path} のassetデータが読み込まれていません。`);
        }
        encodedAssets[path] = await blobToDataUrl(asset.blob);
    }
    return apiRequest("/api/designer/projects", {
        method: "POST",
        body: JSON.stringify({project: validated, assets: encodedAssets}),
    });
}

export async function loadDesktopProject(
    slug: string,
): Promise<{project: ScoreboardProject; assets: AssetMap; slug: string}> {
    const result = await apiRequest<{
        project: ScoreboardProject;
        assets: Record<string, string>;
        slug: string;
    }>(`/api/designer/projects/${encodeURIComponent(slug)}`);
    const project = validateProject(result.project);
    const assets: AssetMap = {};
    for (const [path, dataUrl] of Object.entries(result.assets)) {
        assets[path] = await dataUrlToAsset(path, dataUrl);
    }
    return {project, assets, slug: result.slug};
}

export async function loadLatestFigmaImport(): Promise<{
    project: ScoreboardProject;
    assets: AssetMap;
}> {
    const result = await apiRequest<{
        project: ScoreboardProject;
        assets: Record<string, string>;
    }>("/api/designer/imports/latest");
    const project = validateProject(result.project);
    const assets: AssetMap = {};
    for (const [path, dataUrl] of Object.entries(result.assets)) {
        assets[path] = await dataUrlToAsset(path, dataUrl);
    }
    return {project, assets};
}

export async function loadDesignerImport(importId: string): Promise<{
    project: ScoreboardProject;
    assets: AssetMap;
    report?: {warnings?: string[]; rasterizedLayers?: number};
}> {
    const result = await apiRequest<{
        project: ScoreboardProject;
        assets: Record<string, string>;
        report?: {warnings?: string[]; rasterizedLayers?: number};
    }>(`/api/designer/imports/${encodeURIComponent(importId)}`);
    const project = validateProject(result.project);
    const assets: AssetMap = {};
    for (const [path, dataUrl] of Object.entries(result.assets)) {
        assets[path] = await dataUrlToAsset(path, dataUrl);
    }
    return {project, assets, report: result.report};
}

export async function deleteDesktopProject(
    slug: string,
): Promise<{slug: string}> {
    return apiRequest(
        `/api/designer/projects/${encodeURIComponent(slug)}`,
        {method: "DELETE"},
    );
}
