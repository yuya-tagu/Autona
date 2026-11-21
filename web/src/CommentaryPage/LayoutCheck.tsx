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
const KINDS = ["ready", "onUpdate", "error", "unhandledrejection", "autona"];

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
}

export const EMPTY_REPORT: LayoutReport = {
    ready: false, contract: null, errors: [], dropped: 0};

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

// The hint for an error: a key of the locale files (layout_check_hint_*).
export function hintKey(error: LayoutMessage): string {
    const text = error.message;
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
    const lines = [ja("layout_check_ai_intro", {
        name: design.displayName,
        path: `layout/${design.id}/${design.entry}`,
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
                   `    ${ja("layout_check_hint_label")}: ${ja("layout_check_not_ready_hint")}`,
                   "");
    }
    report.errors.forEach((error, index) => {
        lines.push(`[${index + 1}] ${ja(`layout_check_kind_${error.kind}`)}: ${error.message}`);
        lines.push(`    ${place(error, ja)}`);
        if (error.count > 1) {
            lines.push(`    ${ja("layout_check_count", {count: error.count})}`);
        }
        lines.push(`    ${ja("layout_check_hint_label")}: ${ja(hintKey(error))}`);
        lines.push("");
    });
    if (report.dropped > 0) {
        lines.push(ja("layout_check_dropped", {count: report.dropped}), "");
    }
    lines.push(ja("layout_check_ai_outro"));
    return lines.join("\n");
}

// The errors a shown layout told, or that it did not say it is ready, or that
// it is ready with none; nothing for a layout that says nothing.
export function LayoutCheckPanel({design, report, notReady}: {
    design: LayoutEntry;
    report: LayoutReport;
    // Expected to use autona.js and not ready within READY_TIMEOUT_MS.
    notReady: boolean;
}) {
    const hasErrors = report.errors.length > 0;
    const warnings = design.warnings ?? [];
    if (!hasErrors && !notReady && warnings.length === 0) {
        return report.ready ? (
            <Typography variant="body2" color="success.main" role="status"
                        aria-label={i18n.t("layout_check_title")}>
                {i18n.t("layout_check_clean", {
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
                {i18n.t("layout_check_heading", {
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
                            {`${i18n.t("layout_check_hint_label")}: ${i18n.t("layout_check_not_ready_hint")}`}
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
                            {`${i18n.t("layout_check_hint_label")}: ${i18n.t(hintKey(error))}`}
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
                                `layout_unlisted_${folder.reason}`,
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
