import {BASE_URL} from "../env";
import {
    ShellResult,
    pickFileArgument,
    shellRequest,
} from "../PanelShell/hostApi";
import {
    apiDelete,
    apiGetBlob,
    apiGetJson,
    apiGetText,
    apiPatch,
    apiPost,
    apiPostBytes,
    apiPut,
} from "../api";
import {
    DevState,
    InputChoice,
    InstallStatus,
    PickResult,
    PluginAction,
    PluginDetail,
    PluginListItem,
    PluginRun,
    RemovalInfo,
    RemoveBody,
    RunRequest,
    UploadAnswer,
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
// The picks and the links are shell requests (hostApi.ts shellRequest, in
// the click): pickFile (the field's extensions) and pickFolder (purpose
// "plugin") answer the pick id and `name` (the file or folder name to show,
// never the path; routes_v1.json pick_answer) -- the page says "chosen"
// without one -- or nothing when the dialog was closed (null here).
// openPluginLink names the plugin and the link; the desktop builds the URL.
// A refusal rejects with an Error whose `code` is the desktop's error
// (bad_extension, not_local, busy, not_ready, ...) or shellRequest's own
// (no_session, timeout, superseded).
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
    // The jobs offered at a page's "Plugins" menu (SHELL).
    actions(target: string): Promise<PluginAction[]>;
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
    // Install (P5a): the zip's bytes (raw body, not multipart), then an
    // install of the upload, polled until it needs consent / ends.
    uploadZip(file: Blob): Promise<UploadAnswer>;
    dropUpload(uploadId: string): Promise<void>;
    startInstall(uploadId: string): Promise<InstallStatus>;
    installStatus(installId: string): Promise<InstallStatus>;
    consentInstall(installId: string,
                   consentDigest: string): Promise<InstallStatus>;
    cancelInstall(installId: string): Promise<InstallStatus>;
    setEnabled(id: string, enabled: boolean): Promise<void>;
    removalInfo(id: string): Promise<RemovalInfo>;
    startRemoval(id: string, body: RemoveBody): Promise<RemovalInfo>;
    // Shell requests: null when the dialog was closed.
    pickFile(extensions: string[]): Promise<PickResult | null>;
    pickFolder(): Promise<PickResult | null>;
    openLink(source: "installed", id: string,
             link: "homepage" | "funding"): Promise<void>;
}

const BASE = "/api/v1/plugins";

// A zip is up to limits.zip_max_bytes (2 GiB): give its upload minutes.
export const UPLOAD_TIMEOUT_MS = 600000;

// :reload runs describe, which the host gives up on after 60 s
// (contract_1.json describe_timeout_seconds): wait well beyond it.
export const RELOAD_TIMEOUT_MS = 90000;

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
    actions: (target) => data<PluginAction[]>(
        `${BASE}/actions?target=${enc(target)}`),
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
        // describe may take up to 60 s on the host: wait longer than that.
        await apiPost(`${BASE}/${enc(id)}:reload`, undefined,
                      {timeoutMs: RELOAD_TIMEOUT_MS});
    },
    uploadZip: async (file) => JSON.parse(await apiPostBytes(
        `${BASE}/uploads`, file, {timeoutMs: UPLOAD_TIMEOUT_MS})).data,
    dropUpload: async (uploadId) => {
        await apiDelete(`${BASE}/uploads/${enc(uploadId)}`);
    },
    startInstall: async (uploadId) => JSON.parse(await apiPost(
        `${BASE}/installs`, {upload_id: uploadId})).data,
    installStatus: (installId) => data<InstallStatus>(
        `${BASE}/installs/${enc(installId)}`),
    consentInstall: async (installId, consentDigest) => JSON.parse(
        await apiPost(`${BASE}/installs/${enc(installId)}:consent`,
                      {consent_digest: consentDigest})).data,
    cancelInstall: async (installId) => JSON.parse(await apiPost(
        `${BASE}/installs/${enc(installId)}:cancel`)).data,
    setEnabled: async (id, enabled) => {
        await apiPost(`${BASE}/${enc(id)}:${enabled ? "enable" : "disable"}`);
    },
    removalInfo: (id) => data<RemovalInfo>(`${BASE}/${enc(id)}/removal`),
    startRemoval: async (id, body) => JSON.parse(await apiPost(
        `${BASE}/${enc(id)}:remove`, body)).data,
    pickFile: (extensions) => {
        const argument = pickFileArgument(extensions);
        if (argument === null) {
            // The desktop would refuse it without an answer: not asked.
            return Promise.reject(shellRefusal("bad_argument"));
        }
        return shellRequest("pickFile", {extensions: argument})
            .then(pickAnswer);
    },
    pickFolder: () => shellRequest("pickFolder", {purpose: "plugin"})
        .then(pickAnswer),
    openLink: async (source, id, link) => {
        const result = await shellRequest(
            "openPluginLink", {source, id, link});
        if (!result.ok) {
            throw shellRefusal(result.error ?? "failed");
        }
    },
};

function shellRefusal(code: string): Error & {code: string} {
    return Object.assign(new Error(`shell request refused: ${code}`), {code});
}

// A pick's answer as the page takes it: the pick, null for a closed dialog;
// a refusal throws.
function pickAnswer(result: ShellResult): PickResult | null {
    if (!result.ok) {
        throw shellRefusal(result.error ?? "failed");
    }
    if (result.status !== "picked" || typeof result.id !== "string"
        || !result.id) {
        return null;
    }
    return typeof result.name === "string" && result.name
        ? {id: result.id, name: result.name} : {id: result.id};
}
