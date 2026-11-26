import {BASE_URL} from "../env";
import {withToken} from "../autonaToken";
import {apiDelete, apiGetJson, apiPatch, apiPost, apiPut} from "../api";
import {ShellResult, shellRequest} from "../PanelShell/hostApi";
import {
    DevState,
    InputChoice,
    Pick,
    PluginDetail,
    PluginListItem,
    PluginRun,
    RunRequest,
} from "./pluginTypes";

// Every call the plugins page makes, in one module (ADR-009 D4,
// docs/plugins/routes_v1.json). The page takes a `PluginsApi`, so the
// mock below (mockPluginsApi.ts) and the real one are interchangeable.
//
// Bodies the contract leaves open are fixed here, for the wiring PR to
// match: PATCH settings {values: {key: value | pick id}}; PUT secrets
// {value}; POST runs answers {run_id}; PATCH dev {enabled}; POST dev/folders
// {pick}; GET inputs/<kind> answers [{id, label}].

export interface PluginsApi {
    list(): Promise<PluginListItem[]>;
    iconUrl(id: string): string;
    detail(id: string): Promise<PluginDetail>;
    saveSettings(id: string, values: Record<string, unknown>): Promise<void>;
    saveSecret(id: string, key: string, value: string): Promise<void>;
    clearSecret(id: string, key: string): Promise<void>;
    inputChoices(kind: string): Promise<InputChoice[]>;
    startRun(id: string, request: RunRequest): Promise<{run_id: string}>;
    runs(id: string): Promise<PluginRun[]>;
    run(id: string, runId: string): Promise<PluginRun>;
    cancelRun(id: string, runId: string): Promise<void>;
    runLog(id: string, runId: string): Promise<string>;
    artifactText(id: string, runId: string, no: number): Promise<string>;
    artifactBlob(id: string, runId: string, no: number): Promise<Blob>;
    revealArtifact(id: string, runId: string, no: number): Promise<void>;
    dev(): Promise<DevState>;
    setDev(enabled: boolean): Promise<void>;
    addDevFolder(pickId: string): Promise<void>;
    removeDevFolder(folderId: string): Promise<void>;
    reload(id: string): Promise<void>;
    // Shell requests: null when the dialog was closed.
    pickFile(extensions: string[]): Promise<Pick | null>;
    pickFolder(): Promise<Pick | null>;
    openLink(source: "installed", id: string,
             link: "homepage" | "funding"): Promise<void>;
}

const BASE = "/api/v1/plugins";

function enc(part: string): string {
    return encodeURIComponent(part);
}

async function data<T>(path: string): Promise<T> {
    return (await apiGetJson<{data: T}>(path)).data;
}

async function fetchOk(path: string, init?: RequestInit): Promise<Response> {
    const resp = await fetch(`${BASE_URL}${path}`, {
        ...init,
        headers: withToken(init?.headers),
        signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok) {
        throw Object.assign(new Error(`GET ${path} failed: HTTP ${resp.status}`),
                            {status: resp.status});
    }
    return resp;
}

function pickOf(result: ShellResult): Pick | null {
    if (!result.ok) {
        throw Object.assign(new Error(`pick: ${result.error}`),
                            {code: result.error});
    }
    if (result.status !== "picked" || !result.pick) {
        return null;
    }
    // The desktop may answer the name to show (never the path).
    const name = (result as ShellResult & {name?: string}).name ?? "";
    return {id: result.pick, name};
}

export const httpPluginsApi: PluginsApi = {
    list: () => data<PluginListItem[]>(BASE),
    iconUrl: (id) => `${BASE_URL}${BASE}/${enc(id)}/icon`,
    detail: (id) => data<PluginDetail>(`${BASE}/${enc(id)}`),
    saveSettings: async (id, values) => {
        await apiPatch(`${BASE}/${enc(id)}/settings`, {values});
    },
    saveSecret: async (id, key, value) => {
        await apiPut(`${BASE}/${enc(id)}/secrets/${enc(key)}`, {value});
    },
    clearSecret: async (id, key) => {
        await apiDelete(`${BASE}/${enc(id)}/secrets/${enc(key)}`);
    },
    inputChoices: (kind) => data<InputChoice[]>(`${BASE}/inputs/${enc(kind)}`),
    startRun: async (id, request) => JSON.parse(
        await apiPost(`${BASE}/${enc(id)}/runs`, request)).data,
    runs: (id) => data<PluginRun[]>(`${BASE}/${enc(id)}/runs`),
    run: (id, runId) => data<PluginRun>(
        `${BASE}/${enc(id)}/runs/${enc(runId)}`),
    cancelRun: async (id, runId) => {
        await apiPost(`${BASE}/${enc(id)}/runs/${enc(runId)}:cancel`);
    },
    runLog: async (id, runId) => (await fetchOk(
        `${BASE}/${enc(id)}/runs/${enc(runId)}/log`)).text(),
    artifactText: async (id, runId, no) => (await fetchOk(
        `${BASE}/${enc(id)}/runs/${enc(runId)}/artifacts/${no}`)).text(),
    artifactBlob: async (id, runId, no) => (await fetchOk(
        `${BASE}/${enc(id)}/runs/${enc(runId)}/artifacts/${no}`)).blob(),
    revealArtifact: async (id, runId, no) => {
        await apiPost(
            `${BASE}/${enc(id)}/runs/${enc(runId)}/artifacts/${no}:reveal`);
    },
    dev: () => data<DevState>(`${BASE}/dev`),
    setDev: async (enabled) => {
        await apiPatch(`${BASE}/dev`, {enabled});
    },
    addDevFolder: async (pickId) => {
        await apiPost(`${BASE}/dev/folders`, {pick: pickId});
    },
    removeDevFolder: async (folderId) => {
        await apiDelete(`${BASE}/dev/folders/${enc(folderId)}`);
    },
    reload: async (id) => {
        await apiPost(`${BASE}/${enc(id)}:reload`);
    },
    // pickFile is not a shell verb yet (the wiring adds it to ShellVerb and
    // the desktop): until then the request is refused as an unknown verb.
    pickFile: () => Promise.reject(
        Object.assign(new Error("pickFile is not implemented"),
                      {code: "unknown_verb"})),
    pickFolder: async () => pickOf(await shellRequest("pickFolder")),
    // openPluginLink likewise (ADR-009 D4; stage P5b).
    openLink: () => Promise.reject(
        Object.assign(new Error("openPluginLink is not implemented"),
                      {code: "unknown_verb"})),
};
