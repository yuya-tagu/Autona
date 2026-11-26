// The plugins page's data (ADR-009 D4): the shapes of the v1 plugin routes
// (docs/plugins/routes_v1.json), the manifest's text and field types
// (docs/plugins/contract_1.json field_types / job_record / plugin_list).
// The bodies of the SHELL routes are the page's reading of the contract until
// the wiring PR settles them; each one that the contract leaves open is
// noted where it is used (pluginsApi.ts).

// A text: a string, or locale -> string with at least one entry.
export type PluginText = string | Record<string, string>;

export const PLUGIN_STATES = [
    "ready", "disabled", "blocked", "needs_settings", "incompatible",
    "running"] as const;
export type PluginState = typeof PLUGIN_STATES[number];

export const PLUGIN_BADGES = ["listed", "manual", "dev", "builtin"] as const;
export type PluginBadge = typeof PLUGIN_BADGES[number];

// GET /api/v1/plugins (THIS_PC): an id and enumerations only.
export interface PluginListItem {
    id: string;
    name: PluginText;
    author: string;
    version: string;
    state: PluginState;
    badge: PluginBadge;
    size_bytes: number;
    has_icon: boolean;
}

export const VALUE_TYPES = [
    "text", "textarea", "number", "bool", "select", "file", "folder",
    "secret"] as const;
export type ValueType = typeof VALUE_TYPES[number];

// The source types a manual run picks (state is never picked) and the
// context-only ones (filled by the trigger, never shown as a choice).
export const PICK_SOURCE_TYPES = [
    "match_log", "result_image", "thumbnail_image"] as const;
export const SOURCE_TYPES = [
    "match_log", "match_record", "state", "result_image", "thumbnail_image",
    "archive_export"] as const;
export type SourceType = typeof SOURCE_TYPES[number];

export interface SelectOption {
    value: string;
    label: PluginText;
}

// One item of a params / settings array. `type` is an open name: a type the
// page does not know is shown as unusable, never dropped.
export interface PluginField {
    key: string;
    type: string;
    label: PluginText;
    required?: boolean;
    default?: unknown;
    help?: PluginText;
    max_length?: number;
    min?: number;
    max?: number;
    step?: number;
    options?: SelectOption[];
    extensions?: string[];
}

export interface PluginJob {
    id: string;
    title: PluginText;
    lane: "light" | "heavy";
    timeout_seconds: number;
    manual?: boolean;
    params?: PluginField[];
    actions?: {target: string; label: PluginText}[];
    hooks?: {on: string; label: PluginText}[];
}

export interface PluginPermissions {
    network: string[];
    files: "inputs" | "data" | "user" | "anywhere";
    spawn: boolean;
    autona_api: "none" | "read" | "write";
    note?: PluginText;
}

// What the detail says of a saved setting: a secret is "set" or not; a file /
// folder value is its name only (never the path); the rest the value.
export type SettingValue =
    | {kind: "value"; value: unknown}
    | {kind: "secret"; set: boolean}
    | {kind: "pick"; name: string | null};

// GET /api/v1/plugins/<id> (SHELL).
export interface PluginDetail extends Omit<PluginListItem, "has_icon"> {
    description?: PluginText;
    has_icon: boolean;
    // The state's reason, in words (blocked, incompatible, needs_settings).
    reason?: PluginText | null;
    homepage: boolean;
    funding: boolean;
    permissions: PluginPermissions;
    settings: PluginField[];
    setting_values: Record<string, SettingValue>;
    jobs: PluginJob[];
}

export const RUN_STATES = [
    "running", "done", "error", "failed", "cancelled"] as const;
export type RunState = typeof RUN_STATES[number];

export interface RunArtifact {
    path: string;
    kind: "text" | "image" | "file" | "json";
    label?: PluginText;
}

// contract_1.json job_record (the parts the page reads).
export interface PluginRun {
    run_id: string;
    plugin: string;
    job: string;
    trigger: {kind: string; target?: string | null};
    lane: "light" | "heavy";
    state: RunState;
    reason: string | null;
    started_at: string;
    finished_at: string | null;
    exit_code: number | null;
    stage: {stage: string; label?: PluginText; index?: number;
            count?: number} | null;
    progress: {stage?: string; fraction?: number; overall?: number;
               eta?: number | null} | null;
    log: PluginText | null;
    warnings: PluginText[];
    summary: PluginText | null;
    artifacts: RunArtifact[];
    error: {code: string; message: PluginText; hint?: PluginText | null} | null;
    cancel_requested: boolean;
}

// GET /plugins/inputs/<kind>: one choice of a source type.
export interface InputChoice {
    id: string;
    label: string;
}

export interface DevState {
    enabled: boolean;
    folders: {id: string; name: string}[];
}

// What a pick gave the page: the desktop's id and the name to show (never a
// path), or null when the dialog was closed.
export interface Pick {
    id: string;
    name: string;
}

// The body of POST /plugins/<id>/runs.
export interface RunRequest {
    job: string;
    trigger: {kind: "manual" | "action"; target: string | null};
    params: Record<string, unknown>;
    inputs: Record<string, string>;
}

// The refusal codes of a start (contract_1.json refusals).
export const START_REFUSALS = [
    "busy", "needs_settings", "blocked", "disabled", "incompatible",
    "job_unusable", "invalid_request"] as const;
