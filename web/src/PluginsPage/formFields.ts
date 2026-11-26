import {
    InputChoice,
    PICK_SOURCE_TYPES,
    Pick,
    PluginField,
    SOURCE_TYPES,
    SettingValue,
    VALUE_TYPES,
} from "./pluginTypes";

// The pure half of the form drawn from an array of fields (ADR-009 B.3):
// what a field starts with, whether a value fits it, and the request values.
// FieldForm.tsx draws it; the page's job form, its settings and (I3) the
// action dialog all use the same pair.

export type FieldKind = "value" | "source" | "unknown";

export function fieldKind(field: PluginField): FieldKind {
    if ((VALUE_TYPES as readonly string[]).includes(field.type)) {
        return "value";
    }
    if ((SOURCE_TYPES as readonly string[]).includes(field.type)) {
        return "source";
    }
    return "unknown";
}

// Whether a manual run picks this field (state and the context-only types are
// filled by the host).
export function isPickSource(field: PluginField): boolean {
    return (PICK_SOURCE_TYPES as readonly string[]).includes(field.type);
}

// A file / folder field's state: the pick made now (its id goes to the
// host), or the name of the value already saved.
export interface PickValue {
    pick: Pick | null;
    stored: string | null;
}

export type FormValues = Record<string, unknown>;

export function isPickValue(value: unknown): value is PickValue {
    return typeof value === "object" && value !== null && "pick" in value;
}

// The starting values: the saved one when there is one, else the field's
// default, else empty. A secret always starts empty (typing sets it).
export function initialValues(
    fields: PluginField[],
    saved: Record<string, SettingValue> = {}): FormValues {
    const values: FormValues = {};
    for (const field of fields) {
        const was = saved[field.key];
        switch (field.type) {
            case "bool":
                values[field.key] = was?.kind === "value"
                    ? Boolean(was.value) : Boolean(field.default);
                break;
            case "number": {
                const value = was?.kind === "value" ? was.value : field.default;
                values[field.key] = typeof value === "number"
                    ? String(value) : "";
                break;
            }
            case "file":
            case "folder": {
                const held: PickValue = {
                    pick: null,
                    stored: was?.kind === "pick" ? was.name : null,
                };
                values[field.key] = held;
                break;
            }
            case "text":
            case "textarea":
            case "select": {
                const value = was?.kind === "value" ? was.value : field.default;
                values[field.key] = typeof value === "string" ? value : "";
                break;
            }
            default:
                // A secret and the source types: empty.
                values[field.key] = "";
        }
    }
    return values;
}

// The reason a value does not fit its field (an i18n key suffix), or null.
// `secretSet`: a required secret already saved needs no new value.
export function validateField(
    field: PluginField, value: unknown, secretSet = false): string | null {
    const kind = fieldKind(field);
    if (kind === "unknown") {
        return "unknown_type";
    }
    if (kind === "source") {
        // Only the ones the user picks can be missing.
        return field.required && isPickSource(field) && !value
            ? "required" : null;
    }
    switch (field.type) {
        case "bool":
            return null;
        case "text":
        case "textarea": {
            const text = typeof value === "string" ? value : "";
            const max = field.max_length ?? (field.type === "text" ? 200 : 4000);
            if (field.required && !text.trim()) {
                return "required";
            }
            return text.length > max ? "too_long" : null;
        }
        case "secret": {
            const text = typeof value === "string" ? value : "";
            if (text === "") {
                return field.required && !secretSet ? "required" : null;
            }
            if (/[\r\n]/.test(text)) {
                return "line_break";
            }
            return text.length > 4096 ? "too_long" : null;
        }
        case "select": {
            const text = typeof value === "string" ? value : "";
            if (!text) {
                return field.required ? "required" : null;
            }
            return (field.options ?? []).some((o) => o.value === text)
                ? null : "bad_option";
        }
        case "number": {
            const text = typeof value === "string" ? value.trim() : "";
            if (!text) {
                return field.required ? "required" : null;
            }
            const n = Number(text);
            if (!Number.isFinite(n)) {
                return "not_number";
            }
            if (field.min !== undefined && n < field.min) {
                return "below_min";
            }
            if (field.max !== undefined && n > field.max) {
                return "above_max";
            }
            if (field.step !== undefined && field.step > 0) {
                const base = field.min ?? 0;
                const steps = (n - base) / field.step;
                if (Math.abs(steps - Math.round(steps)) > 1e-9) {
                    return "bad_step";
                }
            }
            return null;
        }
        case "file":
        case "folder": {
            const held = isPickValue(value) ? value : null;
            return field.required && !held?.pick && !held?.stored
                ? "required" : null;
        }
        default:
            return "unknown_type";
    }
}

// The first reason of each invalid field, by key.
export function validateAll(
    fields: PluginField[], values: FormValues,
    secretSet: Record<string, boolean> = {}): Record<string, string> {
    const errors: Record<string, string> = {};
    for (const field of fields) {
        const error = validateField(
            field, values[field.key], Boolean(secretSet[field.key]));
        if (error) {
            errors[field.key] = error;
        }
    }
    return errors;
}

// The request's values: `params` for the value types (a file / folder by its
// pick id; a value saved earlier is the host's, so not sent again) and
// `inputs` for the picked source types. A secret is not a param (it has its
// own route); an empty optional value is left out.
export function collectValues(
    fields: PluginField[], values: FormValues):
    {params: Record<string, unknown>; inputs: Record<string, string>} {
    const params: Record<string, unknown> = {};
    const inputs: Record<string, string> = {};
    for (const field of fields) {
        const value = values[field.key];
        if (fieldKind(field) === "source") {
            if (isPickSource(field) && typeof value === "string" && value) {
                inputs[field.key] = value;
            }
            continue;
        }
        switch (field.type) {
            case "bool":
                params[field.key] = Boolean(value);
                break;
            case "number":
                if (typeof value === "string" && value.trim()) {
                    params[field.key] = Number(value);
                }
                break;
            case "file":
            case "folder":
                if (isPickValue(value) && value.pick) {
                    params[field.key] = value.pick.id;
                }
                break;
            case "secret":
                break;
            default:
                if (typeof value === "string" && value !== "") {
                    params[field.key] = value;
                }
        }
    }
    return {params, inputs};
}

export type ChoiceLoader = (kind: string) => Promise<InputChoice[]>;
