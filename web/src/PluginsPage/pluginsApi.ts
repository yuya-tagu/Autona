import {BASE_URL} from "../env";
import {
    apiDelete,
    apiGetBlob,
    apiGetJson,
    apiGetText,
    apiPatch,
    apiPost,
    apiPut,
} from "../api";
import {
    DevState,
    InputChoice,
    PickResult,
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
//
// PATCH settings sends null for a value the user emptied or let go (a text,
// a number, a select left empty, a file / folder released): the host removes
// the saved value. A key not in the body stays as it is.
//
// A pick's answer (pickFile / pickFolder) carries `name` (the file or folder
// name to show, never the path) next to the pick id; the page says "chosen"
// without one. pickFolder today is the game assets' dialog: L10 (the wiring)
// makes the shell request general for these, so until then both picks are
// unimplemented here, as openPluginLink is.
//
// A busy refusal carries the remaining seconds in its message (`detail`)
// when known; the page shows that text after its own.

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
    pickFile(extensions: string[]): Promise<PickResult | null>;
    pickFolder(): Promise<PickResult | null>;
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
    runLog: (id, runId) => apiGetText(
        `${BASE}/${enc(id)}/runs/${enc(runId)}/log`),
    artifactText: (id, runId, no) => apiGetText(
        `${BASE}/${enc(id)}/runs/${enc(runId)}/artifacts/${no}`),
    artifactBlob: (id, runId, no) => apiGetBlob(
        `${BASE}/${enc(id)}/runs/${enc(runId)}/artifacts/${no}`),
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
    // Neither pick is a general shell verb yet (L10 makes pickFolder and
    // pickFile general): refused as an unknown verb until then.
    pickFile: () => Promise.reject(
        Object.assign(new Error("pickFile is not implemented"),
                      {code: "unknown_verb"})),
    pickFolder: () => Promise.reject(
        Object.assign(new Error("pickFolder is not general yet"),
                      {code: "unknown_verb"})),
    // openPluginLink likewise (ADR-009 D4; stage P5b).
    openLink: () => Promise.reject(
        Object.assign(new Error("openPluginLink is not implemented"),
                      {code: "unknown_verb"})),
};
