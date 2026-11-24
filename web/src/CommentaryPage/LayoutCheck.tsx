import React from "react";
import {Alert, Box, Button, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import {copyText} from "../PanelShell/clipboard";
import {LayoutEntry, ManifestWarning, UnlistedFolder} from "./SideCards";

// The design lists' check of a layout (ADR-008 D3 / L3): what the preview's
// layout told the page (layout/include/autona.js posts its errors and
// "autona:ready" to the page around its frame), shown as text with a copy for
// the operator's AI, and the folders the catalog could not list.
//
// What a frame posts is anyone's data: DesignPane takes only what its own
// frames sent from the preview origin, this module takes only the one shape
// autona.js sends (parseLayoutMessage), every string cut to a length and the
// list to a count, and all of it is rendered as text (no HTML). It moves
// nothing but this panel: no state, no navigation, no clipboard (the copy is
// the operator's click).
//
// A thumbnail template on template contract 2 (ADR-008 D7 R2) is checked the
// same way: its live preview's frame posts what autona.js caught while it
// drew (kind "onRender" among them), and a generate that failed on the
// template's error publishes the same errors (thumbnail.render.errors), which
// reportFromErrors turns into a report for this panel.

export const MESSAGE_MAX = 500;
export const SOURCE_MAX = 300;
// Distinct errors kept for a document; more are counted, not shown.
export const ERRORS_MAX = 20;
// Repeats and dropped errors are counted up to this; past it a message
// changes nothing (a frame posting in a loop costs no render).
export const COUNT_MAX = 999;
// How long a layout expected to use autona.js has to say it is ready.
export const READY_TIMEOUT_MS = 5000;

const MESSAGE_KEYS = ["type", "contract", "kind", "message", "source", "line",
                      "column"];
const TYPES = ["autona:ready", "autona:error"];
const KINDS = ["ready", "onUpdate", "onRender", "error",
               "unhandledrejection", "autona"];

export interface LayoutMessage {
    type: "autona:ready" | "autona:error";
    contract: number | null;
    kind: string;
    message: string;
    source: string;
    line: number | null;
    column: number | null;
}

export interface LayoutError extends LayoutMessage {
    // Which frame said it (the Board's design or its popup).
    frame: string;
    count: number;
}

// What one shown document has told the page.
export interface LayoutReport {
    ready: boolean;
    contract: number | null;
    errors: LayoutError[];
    // Errors past ERRORS_MAX.
    dropped: number;
    // The newest render a template's frame said it drew (autona:rendered;
    // ADR-008 D7 R2), null for none.
    rendered: number | null;
}

export const EMPTY_REPORT: LayoutReport = {
    ready: false, contract: null, errors: [], dropped: 0, rendered: null};

function clip(text: string, max: number): string {
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function lineOrNull(value: unknown): number | null | undefined {
    if (value === null) {
        return null;
    }
    return typeof value === "number" && Number.isInteger(value) && value > 0
        && value < 1e9 ? value : undefined;
}

// The message autona.js posts, or null for anything else: exactly its keys,
// each of its type.
export function parseLayoutMessage(data: unknown): LayoutMessage | null {
    if (typeof data !== "object" || data === null || Array.isArray(data)
            || Object.getPrototypeOf(data) !== Object.prototype) {
        return null;
    }
    const record = data as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length !== MESSAGE_KEYS.length
            || !MESSAGE_KEYS.every((key) => keys.includes(key))) {
        return null;
    }
    const {type, contract, kind, message, source} = record;
    const line = lineOrNull(record.line);
    const column = lineOrNull(record.column);
    if (typeof type !== "string" || !TYPES.includes(type)
            || typeof kind !== "string" || !KINDS.includes(kind)
            || (type === "autona:ready") !== (kind === "ready")
            || typeof message !== "string" || typeof source !== "string"
            || line === undefined || column === undefined
            || !(contract === null || (typeof contract === "number"
                 && Number.isInteger(contract) && contract >= 0
                 && contract < 1000))) {
        return null;
    }
    return {
        type: type as LayoutMessage["type"],
        contract: contract as number | null,
        kind,
        message: clip(message, MESSAGE_MAX),
        source: clip(source, SOURCE_MAX),
        line,
        column,
    };
}

// What autona.js posts once a frame drew a render (ADR-008 D7 R2), or null
// for anything else: exactly {type: "autona:rendered", contract: 2, serial,
// ok}.
const RENDERED_KEYS = ["type", "contract", "serial", "ok"];

export function parseRenderedMessage(
    data: unknown,
): {serial: number; ok: boolean} | null {
    if (typeof data !== "object" || data === null || Array.isArray(data)
            || Object.getPrototypeOf(data) !== Object.prototype) {
        return null;
    }
    const record = data as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length !== RENDERED_KEYS.length
            || !RENDERED_KEYS.every((key) => keys.includes(key))
            || record.type !== "autona:rendered" || record.contract !== 2
            || typeof record.serial !== "number"
            || !Number.isInteger(record.serial) || record.serial < 1
            || typeof record.ok !== "boolean") {
        return null;
    }
    return {serial: record.serial, ok: record.ok};
}

// The report with `message` (from frame `frame`) added: a repeat counts.
export function addMessage(report: LayoutReport, message: LayoutMessage,
                           frame: string): LayoutReport {
    const contract = message.contract ?? report.contract;
    if (message.type === "autona:ready") {
        return {...report, ready: true, contract};
    }
    const same = report.errors.findIndex((known) =>
        known.frame === frame && known.kind === message.kind
        && known.message === message.message && known.source === message.source
        && known.line === message.line && known.column === message.column);
    if (same !== -1) {
        if (report.errors[same].count >= COUNT_MAX) {
            return report;
        }
        const errors = report.errors.slice();
        errors[same] = {...errors[same], count: errors[same].count + 1};
        return {...report, contract, errors};
    }
    if (report.errors.length >= ERRORS_MAX) {
        if (report.dropped >= COUNT_MAX) {
            return report;
        }
        return {...report, contract, dropped: report.dropped + 1};
    }
    return {...report, contract,
            errors: [...report.errors, {...message, frame, count: 1}]};
}

// A thumbnail / result template (ADR-007), not an overlay: its page draws in
// Autona.onRender, and its words say template contract.
export function isTemplate(design: LayoutEntry): boolean {
    return design.kind === "thumbnail" || design.kind === "result";
}

// The words of a template's own page (its entry page's name, what it is
// called): a result's (ADR-008 D7 R4), else the thumbnail's ("_template"),
// else an overlay's ("").
function pageWords(design: LayoutEntry): string {
    if (design.kind === "result") {
        return "_result";
    }
    return isTemplate(design) ? "_template" : "";
}

// The hint for an error of `design`'s, in its page's words.
function designHint(error: LayoutMessage, design: LayoutEntry): string {
    const key = hintKey(error, isTemplate(design));
    return key === "layout_check_hint_on_render_setup"
        && design.kind === "result" ? `${key}_result` : key;
}

// The page's path under the app's folder: a template package's folder is
// not its id (a shipped one's id is not its folder's name).
function designPath(design: LayoutEntry): string {
    return `layout/${design.folder ?? design.id}/${design.entry}`;
}

// The errors a render published (thumbnail.render.errors: core/RenderLane.
// TemplateErrors, line / column 0 when not known) as a report, or null when
// there are none. Each read as a frame's message is (parseLayoutMessage).
export function reportFromErrors(errors: unknown,
                                 contract: number): LayoutReport | null {
    if (!Array.isArray(errors)) {
        return null;
    }
    let report = EMPTY_REPORT;
    errors.forEach((item) => {
        if (typeof item !== "object" || item === null) {
            return;
        }
        const {kind, message, source, line, column} =
            item as Record<string, unknown>;
        const parsed = parseLayoutMessage({
            type: "autona:error", contract, kind, message, source,
            line: line === 0 ? null : line,
            column: column === 0 ? null : column,
        });
        if (parsed !== null) {
            report = addMessage(report, parsed, "render");
        }
    });
    return report.errors.length > 0 ? {...report, contract} : null;
}

// The hint for an error: a key of the locale files (layout_check_hint_*).
// `template`: the error is a template's (isTemplate).
export function hintKey(error: LayoutMessage, template = false): string {
    const text = error.message;
    if (template && /Autona\.onRender|include\/autona\.js/.test(text)) {
        return "layout_check_hint_on_render_setup";
    }
    if (/globals\.js before autona\.js/.test(text)) {
        return "layout_check_hint_globals";
    }
    if (/right after globals\.js/.test(text)) {
        return "layout_check_hint_order";
    }
    if (/autonaSample|no sample named|not a sample name/.test(text)) {
        return "layout_check_hint_sample";
    }
    if (/SyntaxError|Unexpected (token|end|identifier|string|number)|Invalid or unexpected token/.test(text)) {
        return "layout_check_hint_syntax";
    }
    if (/is not defined/.test(text)) {
        return "layout_check_hint_not_defined";
    }
    if (/read[- ]only|not extensible|Cannot assign to/.test(text)) {
        return "layout_check_hint_frozen";
    }
    if (/Cannot read propert|Cannot set propert|is not an object|of undefined|of null/.test(text)) {
        return "layout_check_hint_missing";
    }
    if (error.kind === "onUpdate") {
        return "layout_check_hint_on_update";
    }
    if (error.kind === "onRender") {
        return "layout_check_hint_on_render";
    }
    return "layout_check_hint_generic";
}

// A manifest warning of the catalog's (the entry's `warnings`) in the words
// the list of unlisted folders uses, and its hint.
function warningText(warning: ManifestWarning, t: typeof i18n.t): string {
    return `settings.json: ${t(`layout_unlisted_${warning.reason}`,
                               {defaultValue: t("layout_unlisted_other")})}`;
}

function warningHint(warning: ManifestWarning): string {
    return warning.reason === "bad_entry"
        ? "layout_check_hint_entry" : "layout_check_hint_manifest";
}

function place(error: LayoutMessage, t: typeof i18n.t): string {
    if (!error.source && error.line === null) {
        return t("layout_check_place_unknown");
    }
    return t("layout_check_place", {
        source: error.source || "?",
        line: error.line ?? "?",
        column: error.column ?? "?",
    });
}

// What "copy for an AI" puts on the clipboard: Japanese, as the layout kit's
// instructions are (AGENTS.md), whatever the page's language.
export function aiReport(design: LayoutEntry, report: LayoutReport,
                         notReady: boolean): string {
    const ja = i18n.getFixedT("ja");
    const contract = report.contract ?? design.contract ?? null;
    const template = isTemplate(design);
    const words = template ? "_template" : "";
    const lines = [ja(`layout_check_ai_intro${pageWords(design)}`, {
        name: design.displayName,
        path: designPath(design),
        contract: contract === null ? "—" : String(contract),
    }), ""];
    (design.warnings ?? []).forEach((warning) => {
        lines.push(`[!] ${warningText(warning, ja)}`);
        if (warning.detail) {
            lines.push(`    ${warning.detail}`);
        }
        lines.push(`    ${ja("layout_check_hint_label")}: ${ja(warningHint(warning))}`,
                   "");
    });
    if (notReady) {
        lines.push(`[!] ${ja("layout_check_not_ready")}`,
                   `    ${ja("layout_check_hint_label")}: ${ja(`layout_check_not_ready_hint${pageWords(design)}`)}`,
                   "");
    }
    report.errors.forEach((error, index) => {
        lines.push(`[${index + 1}] ${ja(`layout_check_kind_${error.kind}`)}: ${error.message}`);
        lines.push(`    ${place(error, ja)}`);
        if (error.count > 1) {
            lines.push(`    ${ja("layout_check_count", {count: error.count})}`);
        }
        lines.push(`    ${ja("layout_check_hint_label")}: ${ja(designHint(error, design))}`);
        lines.push("");
    });
    if (report.dropped > 0) {
        lines.push(ja("layout_check_dropped", {count: report.dropped}), "");
    }
    lines.push(ja(`layout_check_ai_outro${words}`));
    return lines.join("\n");
}

// The errors a shown layout told, or that it did not say it is ready, or that
// it is ready with none; nothing for a layout that says nothing.
export function LayoutCheckPanel({design, report, notReady, drawing = false}: {
    design: LayoutEntry;
    report: LayoutReport;
    // Expected to use autona.js and not ready within READY_TIMEOUT_MS.
    notReady: boolean;
    // A template's render posted and not said drawn yet: no "no errors"
    // before it is (ADR-008 D7 R2).
    drawing?: boolean;
}) {
    const hasErrors = report.errors.length > 0;
    const warnings = design.warnings ?? [];
    const template = isTemplate(design);
    const words = template ? "_template" : "";
    if (!hasErrors && !notReady && warnings.length === 0 && drawing) {
        return (
            <Typography variant="body2" color="text.secondary" role="status"
                        aria-label={i18n.t("layout_check_title")}>
                {i18n.t("layout_check_drawing_template", {
                    contract: report.contract ?? design.contract ?? "—"})}
            </Typography>
        );
    }
    if (!hasErrors && !notReady && warnings.length === 0) {
        return report.ready ? (
            <Typography variant="body2" color="success.main" role="status"
                        aria-label={i18n.t("layout_check_title")}>
                {i18n.t(`layout_check_clean${words}`, {
                    contract: report.contract ?? design.contract ?? "—"})}
            </Typography>
        ) : null;
    }
    const contract = report.contract ?? design.contract ?? null;

    // In the click itself: the clipboard takes a write only while the click's
    // activation lasts.
    const copy = () => {
        copyText(aiReport(design, report, notReady)).then((copied) =>
            showSnackbar(i18n.t(copied ? "layout_check_copied"
                                       : "layout_check_copy_failed"),
                         copied ? "success" : "error"));
    };

    return (
        <Alert
            severity={hasErrors ? "error" : "warning"}
            component="section"
            // A region, not an alert: it changes with every error posted.
            role="region"
            aria-label={i18n.t("layout_check_title")}
            action={
                <Button color="inherit" size="small" onClick={copy}>
                    {i18n.t("layout_check_copy")}
                </Button>
            }
            sx={{"& .MuiAlert-message": {minWidth: 0, flex: 1}}}
        >
            <Typography variant="subtitle2" component="h3">
                {i18n.t(`layout_check_heading${words}`, {
                    name: design.displayName,
                    contract: contract === null ? "—" : String(contract),
                })}
            </Typography>
            <Stack spacing={1} sx={{mt: 1}} component="ol" style={{
                paddingLeft: 0, margin: 0, listStyle: "none"}}>
                {warnings.map((warning, index) => (
                    <Box component="li" key={`w${index}`}
                         data-testid="layout-check-warning">
                        <Typography variant="body2">
                            {warningText(warning, i18n.t)}
                        </Typography>
                        {warning.detail && (
                            <Typography variant="caption" component="div" sx={{
                                fontFamily: "monospace", wordBreak: "break-all"}}>
                                {warning.detail}
                            </Typography>
                        )}
                        <Typography variant="caption" color="text.secondary">
                            {`${i18n.t("layout_check_hint_label")}: ${i18n.t(warningHint(warning))}`}
                        </Typography>
                    </Box>
                ))}
                {notReady && (
                    <Box component="li" data-testid="layout-check-not-ready">
                        <Typography variant="body2">
                            {i18n.t("layout_check_not_ready")}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                            {`${i18n.t("layout_check_hint_label")}: ${i18n.t(`layout_check_not_ready_hint${pageWords(design)}`)}`}
                        </Typography>
                    </Box>
                )}
                {report.errors.map((error, index) => (
                    <Box component="li" key={index} data-testid="layout-check-error">
                        <Typography variant="body2" sx={{
                            fontFamily: "monospace", wordBreak: "break-word"}}>
                            {`${i18n.t(`layout_check_kind_${error.kind}`)}: ${error.message}`}
                        </Typography>
                        <Typography variant="caption" component="div" sx={{
                            fontFamily: "monospace", wordBreak: "break-all"}}>
                            {place(error, i18n.t)
                                + (error.count > 1
                                    ? ` · ${i18n.t("layout_check_count", {count: error.count})}`
                                    : "")}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                            {`${i18n.t("layout_check_hint_label")}: ${i18n.t(designHint(error, design))}`}
                        </Typography>
                    </Box>
                ))}
            </Stack>
            {report.dropped > 0 && (
                <Typography variant="caption" component="p" sx={{mt: 1}}>
                    {i18n.t("layout_check_dropped", {count: report.dropped})}
                </Typography>
            )}
        </Alert>
    );
}

// Why a folder is not listed, as a words key. A template's folder (result_ /
// thumbnail_) with no block is told so in a template's words: the overlay's
// words name the overlay folder names, which say nothing to it.
function unlistedReasonKey(folder: UnlistedFolder): string {
    if (folder.reason === "no_manifest"
            && (folder.kind === "result" || folder.kind === "thumbnail")) {
        return "layout_unlisted_no_manifest_template";
    }
    return `layout_unlisted_${folder.reason}`;
}

// The folders of the page's kinds (and of none known) the catalog did not
// list, with why: under the design select.
export function UnlistedDesigns({folders}: {folders: UnlistedFolder[]}) {
    if (folders.length === 0) {
        return null;
    }
    const title = i18n.t("layout_unlisted_title");
    return (
        <Alert severity="info" component="section" role="region"
               aria-label={title}
               sx={{"& .MuiAlert-message": {minWidth: 0}}}>
            <Typography variant="subtitle2" component="h3">{title}</Typography>
            <Box component="ul" sx={{pl: 2, my: 0.5}}>
                {folders.map((folder) => (
                    <li key={folder.folder}>
                        <Typography variant="body2" sx={{wordBreak: "break-word"}}>
                            {`layout/${folder.folder}/ — ${i18n.t(
                                unlistedReasonKey(folder),
                                {defaultValue: i18n.t("layout_unlisted_other")})}`}
                        </Typography>
                        {folder.detail && (
                            <Typography variant="caption" component="div" sx={{
                                fontFamily: "monospace", wordBreak: "break-all"}}>
                                {folder.detail}
                            </Typography>
                        )}
                    </li>
                ))}
            </Box>
        </Alert>
    );
}
