import React from "react";
import i18n from "../i18n/config";
import {getWithRetry} from "./requests";

// The result's layouts and the thumbnail's templates as GET /api/v1/layouts
// lists them (core/LayoutCatalog, ADR-007 T1): the template packages under
// layout/, the shipped ones first. What the Result and Thumbnail pages offer
// to choose from.

export type TemplateKind = "result" | "thumbnail";

export interface TemplatePackage {
    id: string;
    kind: TemplateKind;
    // The name in the app's language.
    displayName: string;
    // The name in Japanese and in English, the languages there are. A
    // shipped package's is core's words; the page's own words come first
    // (templateLabel).
    displayNames: {ja?: string; en?: string};
    // One of the shipped eight, whose id never changes.
    bundled: boolean;
    size: [number, number];
}

// One choice of a page's pulldown: what is sent, and what is shown.
export interface TemplateChoice {
    id: string;
    label: string;
}

// A package's names in the page's language, else in the other one.
export function displayText(name: TemplatePackage["displayNames"] | undefined,
                            language: string): string {
    if (!name || typeof name !== "object") {
        return "";
    }
    const order = language.startsWith("ja") ? ["ja", "en"] : ["en", "ja"];
    for (const key of order) {
        const text = name[key as "ja" | "en"];
        if (typeof text === "string" && text) {
            return text;
        }
    }
    return "";
}

// What a package is called on the page: a shipped one by the page's own words
// (`<prefix><id>`, as before the catalog), any other by its names in the
// page's language (`displayNames`; the app's language, `displayName`, when it
// has none). Only a shipped one: another's id is a folder's name, which may
// be the end of any key ("field" of result_layout_field).
export function templateLabel(entry: TemplatePackage, prefix: string,
                              language: string): string {
    const own = displayText(entry.displayNames, language)
        || (typeof entry.displayName === "string" ? entry.displayName : "")
        || entry.id;
    return entry.bundled
        ? i18n.t(`${prefix}${entry.id}`, {defaultValue: own})
        : own;
}

// The packages of `kind`, read as the page loads (and again when `loads`
// changes); null until they are, and when they could not be read (or the
// answer is not a list): the page then offers what its own description lists.
export function useTemplatePackages(kind: TemplateKind,
                                    loads: number): TemplatePackage[] | null {
    const [packages, setPackages] =
        React.useState<TemplatePackage[] | null>(null);
    React.useEffect(() => {
        let live = true;
        getWithRetry<{ok: boolean; data: unknown}>(
            `/api/v1/layouts?kind=${kind}`, () => live).then((body) => {
            const data = body?.data;
            if (!Array.isArray(data)) {
                throw new Error(`GET /api/v1/layouts?kind=${kind}: not a list`);
            }
            if (live) {
                setPackages((data as TemplatePackage[]).filter(
                    (entry) => entry && entry.kind === kind));
            }
        }).catch((error) => console.error(error));
        return () => {
            live = false;
        };
    }, [kind, loads]);
    return packages;
}
