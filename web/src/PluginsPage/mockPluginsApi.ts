import {PluginsApi} from "./pluginsApi";
import {
    ConsentSheet,
    DevState,
    InstallStatus,
    PluginDetail,
    PluginListItem,
    PluginRun,
    RemovalInfo,
    RunRequest,
} from "./pluginTypes";

// An in-memory PluginsApi for developing the page before the host's routes
// exist, and for the tests. A started run is "running" for `runningReads`
// reads of it, then done with a text, an image and a file artifact.

const WEBHOOK: PluginDetail = {
    id: "taguyan.webhook",
    name: {ja: "Webhook 送信", en: "Webhook post"},
    description: {
        ja: "リザルト画像と試合の結果を Webhook に送ります。",
        en: "Posts result images and match results to a webhook."},
    author: "taguyan",
    version: "1.0.0",
    state: "ready",
    badge: "listed",
    size_bytes: 3 * 1024 * 1024,
    has_icon: false,
    reason: null,
    homepage: true,
    funding: false,
    permissions: {
        network: ["discord.com", "hooks.slack.com"], files: "inputs",
        spawn: false, autona_api: "none",
        note: {ja: "受け取った画像と文を Webhook に送ります。",
               en: "Sends the received image and text to the webhook."}},
    settings: [
        {key: "webhook_url", type: "secret", required: true,
         label: {ja: "Webhook の URL", en: "Webhook URL"}},
        {key: "mention", type: "select", default: "none",
         label: {ja: "メンション", en: "Mention"},
         options: [{value: "none", label: {ja: "しない", en: "None"}},
                   {value: "here", label: "@here"}]},
    ],
    setting_values: {
        webhook_url: {kind: "secret", set: false},
        mention: {kind: "value", value: "none"}},
    jobs: [
        {id: "post_image", title: {ja: "リザルト画像を送る",
                                   en: "Post the result image"},
         lane: "light", timeout_seconds: 60,
         params: [
             {key: "image", type: "result_image", required: true,
              label: {ja: "リザルト画像", en: "Result image"}},
             {key: "message", type: "textarea", default: "",
              label: {ja: "本文", en: "Message"}, max_length: 1500}],
         actions: [{target: "result_image",
                    label: {ja: "Webhook に送る", en: "Send to webhook"}}]},
        {id: "export_log", title: {ja: "ログを書き出す", en: "Export the log"},
         lane: "light", timeout_seconds: 30,
         params: [
             {key: "log", type: "match_log", required: true,
              label: {ja: "試合の記録", en: "Match log"}},
             {key: "folder", type: "folder",
              label: {ja: "保存先", en: "Folder"}},
             {key: "count", type: "number", min: 1, max: 10, step: 1,
              default: 3, label: {ja: "件数", en: "Count"}}]},
    ],
};

const DEV: PluginDetail = {
    ...WEBHOOK,
    id: "me.sandbox",
    name: "Sandbox",
    author: "me",
    badge: "dev",
    state: "needs_settings",
    reason: {ja: "設定が要ります。", en: "Settings are needed."},
    size_bytes: 40 * 1024,
    homepage: false,
    jobs: [WEBHOOK.jobs[1]],
};

function clone<T>(value: T): T {
    return JSON.parse(JSON.stringify(value));
}

function listItem(detail: PluginDetail): PluginListItem {
    return {
        id: detail.id, name: detail.name, author: detail.author,
        version: detail.version, state: detail.state, badge: detail.badge,
        size_bytes: detail.size_bytes, has_icon: detail.has_icon};
}

// The sheet of the zip the mock "uploads" (a new plugin).
export const MOCK_SHEET: ConsentSheet = {
    id: "acme.hello", version: "1.2.0", name: {ja: "こんにちは", en: "Hello"},
    author: "acme", license: "MIT", description: null, homepage: false,
    source: {kind: "manual", name: null}, verified: false,
    sha256: "0".repeat(64), zip_bytes: 2048, size_bytes: 4096,
    decision: "new_install", needs_consent: true, installed_version: null,
    consent_digest: "digest-1",
    permissions: {network: ["example.com"], files: "inputs", spawn: false,
                  autona_api: "none"},
    settings: [], jobs: []};

export function createMockPluginsApi(
    options: {runningReads?: number; installReads?: number;
              sheet?: ConsentSheet} = {}): PluginsApi {
    const installReads = options.installReads ?? 1;
    const sheet = options.sheet ?? MOCK_SHEET;
    let install: {status: InstallStatus; reads: number} | null = null;
    let removal: RemovalInfo = {
        state: "idle", error: null, data_bytes: 2048, runs: 3,
        runs_bytes: 9000, out_bytes: 4000, work_folder: "acme-work"};
    let removalReads = 0;
    const runningReads = options.runningReads ?? 2;
    const details = new Map<string, PluginDetail>(
        [WEBHOOK, DEV].map((d) => [d.id, clone(d)]));
    const runs = new Map<string, {record: PluginRun; reads: number}[]>();
    let dev: DevState = {enabled: false, folders: []};
    let serial = 0;

    const need = (id: string): PluginDetail => {
        const found = details.get(id);
        if (!found) {
            throw Object.assign(new Error("not found"), {status: 404});
        }
        return found;
    };
    const entry = (id: string, runId: string) => {
        const found = (runs.get(id) ?? []).find(
            (e) => e.record.run_id === runId);
        if (!found) {
            throw Object.assign(new Error("not found"), {status: 404});
        }
        return found;
    };

    return {
        list: async () => [...details.values()].map(listItem),
        iconUrl: (id) => `mock-icon:${id}`,
        detail: async (id) => clone(need(id)),
        saveSettings: async (id, values) => {
            const detail = need(id);
            for (const [key, value] of Object.entries(values)) {
                // null clears the saved value.
                if (value === null) {
                    delete detail.setting_values[key];
                    continue;
                }
                detail.setting_values[key] = typeof value === "string"
                    && detail.settings.find((f) => f.key === key)?.type
                        === "folder"
                    ? {kind: "pick", name: value}
                    : {kind: "value", value};
            }
        },
        saveSecret: async (id, key) => {
            need(id).setting_values[key] = {kind: "secret", set: true};
        },
        clearSecret: async (id, key) => {
            need(id).setting_values[key] = {kind: "secret", set: false};
        },
        inputChoices: async (kind) => kind === "match_log"
            ? [{id: "2026-11-26/cup", label: "2026-11-26 Fake Cup"}]
            : [{id: "r1", label: "result_1.png"}],
        actions: async (target) => [...details.values()].flatMap((d) =>
            d.jobs.filter((j) => (j.actions ?? []).some(
                (a) => a.target === target))
                .map((job) => ({plugin: d.id, name: d.name,
                                job: clone(job)}))),
        startRun: async (id, request: RunRequest) => {
            serial += 1;
            const runId = `20261126-10000${serial}-ab${serial}z`;
            const record: PluginRun = {
                run_id: runId, plugin: id, job: request.job,
                trigger: request.trigger, lane: "light", state: "running",
                reason: null, started_at: "2026-11-26T10:00:00+09:00",
                finished_at: null, exit_code: null,
                stage: {stage: "send", label: "Sending", index: 1, count: 2},
                progress: {overall: 0.3, eta: 5}, log: "working",
                warnings: [], summary: null, artifacts: [], error: null,
                cancel_requested: false};
            runs.set(id, [{record, reads: 0}, ...(runs.get(id) ?? [])]);
            return {run_id: runId};
        },
        runs: async (id) => (runs.get(id) ?? []).map((e) => e.record),
        run: async (id, runId) => {
            const e = entry(id, runId);
            e.reads += 1;
            if (e.record.state === "running" && e.reads > runningReads) {
                e.record = {
                    ...e.record, state: "done", exit_code: 0,
                    finished_at: "2026-11-26T10:00:05+09:00",
                    stage: null, progress: null, summary: "Sent 1 message",
                    artifacts: [
                        {path: "report.txt", kind: "text",
                         label: {ja: "報告", en: "Report"}},
                        {path: "shot.png", kind: "image"},
                        {path: "data.json", kind: "json"}]};
            }
            return e.record;
        },
        cancelRun: async (id, runId) => {
            const e = entry(id, runId);
            e.record = {...e.record, state: "cancelled",
                        finished_at: "2026-11-26T10:00:02+09:00"};
        },
        runLog: async () => "unknown stdout line\nstderr line\n",
        artifactText: async () => "Sent to the webhook.\nOK",
        artifactBlob: async () => new Blob(["x"], {type: "image/png"}),
        revealArtifact: async () => undefined,
        dev: async () => clone(dev),
        setDev: async (enabled) => {
            dev = {...dev, enabled};
        },
        addDevFolder: async (pickId) => {
            dev = {...dev, folders: [...dev.folders,
                                     {id: "0123456789ab", name: pickId}]};
        },
        removeDevFolder: async (folderId) => {
            dev = {...dev, folders: dev.folders.filter(
                (f) => f.id !== folderId)};
        },
        reload: async () => undefined,
        uploadZip: async () => ({upload_id: "up-1", expires_in_sec: 600}),
        dropUpload: async () => undefined,
        startInstall: async () => {
            install = {reads: 0, status: {
                install_id: "in-1", state: "preparing", plugin_id: null,
                sheet: null, error: null}};
            return install.status;
        },
        installStatus: async () => {
            if (!install) {
                throw Object.assign(new Error("not found"), {status: 404});
            }
            install.reads += 1;
            if (install.status.state === "preparing"
                && install.reads > installReads) {
                install.status = {...install.status, state: "consent",
                                  plugin_id: sheet.id, sheet};
            } else if (install.status.state === "installing"
                       && install.reads > installReads) {
                details.set(sheet.id, {
                    ...clone(WEBHOOK), id: sheet.id, name: sheet.name,
                    version: sheet.version, badge: "manual"});
                install.status = {...install.status, state: "done"};
            }
            return install.status;
        },
        consentInstall: async (_id, digest) => {
            if (!install || digest !== sheet.consent_digest) {
                throw Object.assign(new Error("bad"), {
                    status: 400, reason: "invalid_request"});
            }
            install.reads = 0;
            install.status = {...install.status, state: "installing"};
            return install.status;
        },
        cancelInstall: async () => {
            if (install) {
                install.status = {...install.status, state: "cancelled"};
            }
            return install!.status;
        },
        setEnabled: async (id, enabled) => {
            need(id).state = enabled ? "ready" : "disabled";
        },
        removalInfo: async () => {
            if (removal.state === "removing") {
                removalReads += 1;
                if (removalReads > 1) {
                    throw Object.assign(new Error("gone"), {status: 404});
                }
            }
            return {...removal};
        },
        startRemoval: async (id) => {
            removal = {...removal, state: "removing"};
            removalReads = 0;
            details.delete(id);
            return {...removal};
        },
        pickFile: async () => ({id: "pick-file-1", name: "picked.png"}),
        pickFolder: async () => ({id: "pick-folder-1", name: "MyFolder"}),
        openLink: async () => undefined,
    };
}
