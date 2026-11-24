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
    // Its template contract (ADR-007 D3): 2 is drawn in Autona.onRender and
    // previewed live (ADR-008 D7 R2), 1 as an image.
    contract?: number;
    // Its folder under layout/ and its entry page there.
    folder?: string;
    entry?: string;
}

// A folder of the kind (or of none known) the catalog did not list, and why
// (GET /api/v1/layouts' "unlisted", ADR-008 D3): the shape of
// CommentaryPage/SideCards' UnlistedFolder.
export interface UnlistedTemplateFolder {
    folder: string;
    kind: string | null;
    reason: string;
    detail: string | null;
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
    return useTemplatePackagesRead(kind, loads).packages;
}

// The same, whether the reading is over (read, or given up on): a page that
// previews by the package's contract waits for it -- the folders of `kind`
// not listed ([] until read, and when the answer has none: one of no kind
// known is an overlay page's, ADR-008 D7 R3), and how many readings are over
// (`revision`, a failed one too: what a page waits on to act on a reading it
// asked for).
export interface TemplatePackagesRead {
    packages: TemplatePackage[] | null;
    settled: boolean;
    unlisted: UnlistedTemplateFolder[];
    revision: number;
}

export function useTemplatePackagesRead(kind: TemplateKind,
                                        loads: number): TemplatePackagesRead {
    const [read, setRead] = React.useState<TemplatePackagesRead>(
        {packages: null, settled: false, unlisted: [], revision: 0});
    React.useEffect(() => {
        let live = true;
        getWithRetry<{ok: boolean; data: unknown; unlisted?: unknown}>(
            `/api/v1/layouts?kind=${kind}`, () => live).then((body) => {
            const data = body?.data;
            if (!Array.isArray(data)) {
                throw new Error(`GET /api/v1/layouts?kind=${kind}: not a list`);
            }
            if (live) {
                const unlisted = Array.isArray(body.unlisted)
                    ? (body.unlisted as UnlistedTemplateFolder[]).filter(
                        (folder) => folder && folder.kind === kind)
                    : [];
                setRead((current) => ({
                    packages: (data as TemplatePackage[]).filter(
                        (entry) => entry && entry.kind === kind),
                    settled: true, unlisted,
                    revision: current.revision + 1}));
            }
        }).catch((error) => {
            console.error(error);
            if (live) {
                setRead((current) => ({...current, settled: true,
                                       revision: current.revision + 1}));
            }
        });
        return () => {
            live = false;
        };
    }, [kind, loads]);
    return read;
}
