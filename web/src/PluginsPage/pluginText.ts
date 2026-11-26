import {PluginText} from "./pluginTypes";

// A manifest text as the host shows it (contract_1.json text.fallback): the
// current locale, then en, then ja, then the first value.
export function pluginText(text: PluginText | null | undefined,
                           language: string): string {
    if (text === null || text === undefined) {
        return "";
    }
    if (typeof text === "string") {
        return text;
    }
    const base = language.split("-")[0];
    for (const key of [language, base, "en", "ja"]) {
        const value = text[key];
        if (typeof value === "string") {
            return value;
        }
    }
    const first = Object.values(text).find((v) => typeof v === "string");
    return first ?? "";
}

// "1.2 MB" / "340 KB" (MiB / KiB, as Windows' Explorer counts).
export function sizeLabel(bytes: number): string {
    if (bytes >= 1024 * 1024) {
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }
    return `${Math.max(0, Math.round(bytes / 1024))} KB`;
}

// A run's start (ISO 8601 with offset) in the viewer's locale; the raw text
// when it does not parse.
export function startedText(iso: string, language: string): string {
    const time = new Date(iso);
    if (Number.isNaN(time.getTime())) {
        return iso;
    }
    return time.toLocaleString(language);
}
