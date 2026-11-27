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
    // null: no time limit (the built-in vodsplit only).
    timeout_seconds: number | null;
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
    // The run ended on its own before the stop reached it.
    finished_before_cancel?: boolean;
}

// The host's end reasons the page words (contract_1.json end).
export const RUN_REASONS = [
    "launch_failed", "entry_missing", "timed_out", "memory_limit",
    "abnormal_exit", "autona_exited"] as const;

// GET /plugins/inputs/<kind>: one choice of a source type.
export interface InputChoice {
    id: string;
    label: string;
}

export interface DevState {
    enabled: boolean;
    // from_env: AUTONA_PLUGIN_DEV's folder, which the page cannot remove.
    folders: {id: string; name: string; from_env?: boolean}[];
}

// What a pick gave the page: the desktop's id and the name to show (never a
// path), or null when the dialog was closed.
export interface PickResult {
    id: string;
    // The name to show; the contract is to carry it in the pick's answer.
    // Without one the page says "chosen".
    name?: string;
}

// The three places a job can be offered as an action (ADR-009 B.4): the
// pages' "Plugins" menus.
export const ACTION_TARGETS = [
    "result_image", "thumbnail_image", "archive_export"] as const;
export type ActionTarget = typeof ACTION_TARGETS[number];

// GET /api/v1/plugins/actions?target= : one job that offers itself at the
// target, with the plugin's name and the job's form (routes_v1.json).
export interface PluginAction {
    plugin: string;
    name: PluginText;
    job: PluginJob;
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

// ---- Install, enable and removal (ADR-009 P5a; routes_v1.json types) ----

export const INSTALL_STATES = [
    "preparing", "consent", "installing", "done", "failed",
    "cancelled"] as const;
export type InstallState = typeof INSTALL_STATES[number];

export const INSTALL_DECISIONS = [
    "update", "confirm_same_or_older", "new_install", "refuse_dev_id",
    "refuse_reserved"] as const;
export type InstallDecision = typeof INSTALL_DECISIONS[number];

// POST /plugins/uploads answers 201 with this.
export interface UploadAnswer {
    upload_id: string;
    expires_in_sec: number;
}

// InstallFailure: `code` is contract_1.json install.errors' key, `problems`
// the zip problems / manifest check codes / describe's failure.
export interface InstallFailure {
    reason: string;
    code: string;
    problems: string[];
    detail: string;
}

export interface ConsentSheet {
    id: string;
    version: string;
    name: PluginText;
    author: string;
    license: string;
    description: PluginText | null;
    homepage: boolean;
    source: {kind: "github" | "manual"; name: string | null};
    verified: boolean;
    sha256: string;
    zip_bytes: number;
    size_bytes: number;
    decision: InstallDecision;
    needs_consent: boolean;
    installed_version: string | null;
    consent_digest: string;
    permissions: PluginPermissions;
    settings: PluginField[];
    jobs: PluginJob[];
}

export interface InstallStatus {
    install_id: string;
    state: InstallState;
    plugin_id: string | null;
    sheet: ConsentSheet | null;
    error: InstallFailure | null;
}

export const REMOVAL_STATES = ["idle", "removing", "failed"] as const;
export type RemovalState = typeof REMOVAL_STATES[number];

export interface RemovalInfo {
    state: RemovalState;
    error: InstallFailure | null;
    data_bytes: number;
    runs: number;
    runs_bytes: number;
    out_bytes: number;
    // The work folder's name, never its path.
    work_folder: string | null;
}

export interface RemoveBody {
    data: boolean;
    runs: "delete" | "keep_out" | "keep";
}

// A job hook's row (GET /api/v1/plugins/<id>/hooks, ADR-009 D.6; the core's
// _HookJson). `params` holds the saved values of the job's value fields, a
// file / folder by its name.
export interface HookHistoryEntry {
    at: string;
    // done | error | failed | cancelled | overwritten | discarded |
    // skipped_rate_limit | needs_settings
    outcome: string;
    summary: PluginText | null;
    reason: string | null;
    run_id: string | null;
}

export interface HookRow {
    // "<job id>/<on>"
    key: string;
    job: string;
    on: string;
    enabled: boolean;
    params: Record<string, unknown>;
    pending: boolean;
    failures: number;
    // user | rate_limit | failures | reconsent | new_install | blocked |
    // removed | params_changed
    disabled_reason: string | null;
    last_error: {
        code: string; message: PluginText; hint?: PluginText | null;
    } | null;
    history: HookHistoryEntry[];
}
