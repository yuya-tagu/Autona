import React from "react";
import {Checkbox, FormControlLabel, IconButton, Menu, MenuItem} from "@mui/material";
import {Visibility} from "@mui/icons-material";
import {shallowEqual, useSelector} from "react-redux";
import {apiPost} from "../api";
import {showSnackbar} from "./Feedback";
import i18n from "../i18n/config";

// Scoreboard field visibility, shared with the desktop eye menu.
//
// The desktop app mirrors every toggle into StateManager under
// `display_options.show_*` (broadcast to all clients) and accepts remote
// toggles via POST /display-option. Keys and defaults are defined in
// src/core/StateSchema.py (DISPLAY_OPTIONS); this list is the copy that
// cannot import it. tests/test_state_schema.py fails if the two drift.
export const DISPLAY_OPTIONS = [
    {key: "show_sponsor",    labelKey: "show_sponsor",    default: true},
    {key: "show_name",       labelKey: "show_name",       default: true},
    {key: "show_seed",       labelKey: "show_seed",       default: true},
    {key: "show_characters", labelKey: "show_characters", default: true},
    {key: "show_social",     labelKey: "show_twitter",    default: true},
    {key: "show_real_name",  labelKey: "show_real_name",  default: false},
    {key: "show_location",   labelKey: "show_location",   default: true},
    {key: "show_additional", labelKey: "show_additional", default: false},
];

/** Current visibility flags from the synced program state (with the
 *  desktop defaults for keys the server hasn't broadcast yet). */
export function useDisplayOptions() {
    return useSelector((state) => {
        const opts = state.autonaState.autonaState?.display_options || {};
        const result = {};
        for (const o of DISPLAY_OPTIONS) {
            result[o.key] = opts[o.key] ?? o.default;
        }
        return result;
    }, shallowEqual);
}

/** Toggle a field on the desktop side; the change echoes back through the
 *  program-state broadcast (no local optimistic state needed). */
export function setDisplayOption(option, value) {
    return apiPost('/display-option', {option, value})
        .catch((e) => {
            console.error(e);
            showSnackbar(i18n.t("request_failed"));
        });
}

/**
 * Eye-menu button: the desktop eye menu's Web counterpart. The toggles are
 * global (shared display_options), so it can live anywhere; currently it
 * sits in each player's details dialog.
 */
export function DisplayOptionsMenuButton(props) {
    const [anchor, setAnchor] = React.useState(null);
    const options = useDisplayOptions();

    return (
        <>
            <IconButton
                size="small"
                title={i18n.t("visibility_settings")}
                onClick={(e) => setAnchor(e.currentTarget)}
                {...props}
            >
                <Visibility />
            </IconButton>
            <Menu
                anchorEl={anchor}
                open={Boolean(anchor)}
                onClose={() => setAnchor(null)}
            >
                {DISPLAY_OPTIONS.map(o => (
                    <MenuItem key={o.key} onClick={() => setDisplayOption(o.key, !options[o.key])} dense>
                        <FormControlLabel
                            control={<Checkbox checked={options[o.key]} size="small" />}
                            label={i18n.t(o.labelKey)}
                            sx={{pointerEvents: 'none'}}
                        />
                    </MenuItem>
                ))}
            </Menu>
        </>
    );
}
