import sections from "./navSections.json";
import {BASE_URL} from "../env";

// The AppShell's navigation (ADR-005 S-final SF-5a-3): the Qt sidebar's
// items, in its sections and order, with its icons (Window._navSections in
// src/Autona.py). navSections.json is the one table: tests/test_app_shell.py
// holds it to _navSections (the panels, the icons and the translations of
// the headings and the labels -- i18next's app_* against the Qt .ts files'
// "app" context) and every panel to WebAccess.WEB_PANELS.

export interface NavItem {
    panel: string;
    // An icon of assets/icons/.
    icon: string;
    // The label's i18next key.
    label: string;
}

export interface NavSection {
    // The heading's i18next key.
    heading: string;
    items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = sections;

// The pages the Help & Settings menu opens, not sidebar items: none of the
// navigation is highlighted while one shows (Window.MENU_PAGES; the Qt
// sidebar's clearCurrent).
export const MENU_PAGES = ["settings", "api", "companion", "obs", "about",
                           "assets"];

export function iconUrl(icon: string): string {
    return `${BASE_URL}/assets/icons/${encodeURIComponent(icon)}`;
}
