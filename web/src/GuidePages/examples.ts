import rows from "./examples.json";
import {KeyIcon} from "./keyIcon";

// The Companion and OBS guides' example requests (ADR-005 S-final SF-4a-2),
// one table for both (each window had its own copy until then):
// examples.json, whose every method and path tests/test_api_v1_guides.py
// holds to a /api/v1 route. `guides` names the pages that list the row;
// the label is the translation guide_example_<id>.

export type Guide = "companion" | "obs";

export interface Example {
    id: string;
    method: string;
    // Below the API's base URL.
    path: string;
    body: Record<string, unknown> | null;
    icon: KeyIcon;
    guides: Guide[];
}

export const EXAMPLES = rows as Example[];

export function examplesOf(guide: Guide): Example[] {
    return EXAMPLES.filter((row) => row.guides.includes(guide));
}
