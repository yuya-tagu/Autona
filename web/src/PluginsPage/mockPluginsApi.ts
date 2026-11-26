import {PluginsApi} from "./pluginsApi";
import {
    DevState,
    PluginDetail,
    PluginListItem,
    PluginRun,
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

export function createMockPluginsApi(
    options: {runningReads?: number} = {}): PluginsApi {
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
        pickFile: async () => ({id: "pick-file-1", name: "picked.png"}),
        pickFolder: async () => ({id: "pick-folder-1", name: "MyFolder"}),
        openLink: async () => undefined,
    };
}
