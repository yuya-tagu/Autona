import React from "react";
import {Box, MenuItem, Stack, TextField, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {LayoutEntry} from "./SideCards";

// The Commentary page's right pane (ADR-005 S4b): the design select and the
// overlay itself as the preview -- /layout/<design>/<entry>, the page OBS
// shows, so the preview is what goes on air. Scaled down from 1920x1080;
// it does not take clicks. (The Qt preview's copy / save-as-image buttons
// are not here: dropped for the Web version, owner's call 2026-10-31.)
// The Top 8 page's too (S4i), with its own heading and empty text.

const OVERLAY_WIDTH = 1920;
const OVERLAY_HEIGHT = 1080;

function layoutPath(design: LayoutEntry): string {
    const id = design.id.split("/").map(encodeURIComponent).join("/");
    return `/layout/${id}/${encodeURIComponent(design.entry)}`;
}

function usePreviewScale(
    frame: React.RefObject<HTMLDivElement | null>,
    shown: boolean,
): number {
    const [scale, setScale] = React.useState(0.25);
    React.useLayoutEffect(() => {
        const element = frame.current;
        if (!element) {
            return undefined;
        }
        const measure = () => {
            if (element.clientWidth > 0) {
                setScale(element.clientWidth / OVERLAY_WIDTH);
            }
        };
        measure();
        if (typeof ResizeObserver === "undefined") {
            return undefined;
        }
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [frame, shown]);
    return scale;
}

export default function DesignPane({designs, design, onSelect, label, empty}: {
    designs: LayoutEntry[];
    design: LayoutEntry | null;
    onSelect: (id: string) => void;
    // The heading (and the select's name), and what shows without a
    // design: the Commentary page's by default.
    label?: string;
    empty?: string;
}) {
    const frame = React.useRef<HTMLDivElement>(null);
    const scale = usePreviewScale(frame, design !== null);
    const heading = label ?? i18n.t("commentary_design");

    return (
        <Stack spacing={1}>
            <Typography variant="subtitle1" component="h2">{heading}</Typography>
            <TextField
                select
                size="small"
                // The select's own name (no floating label: the heading).
                slotProps={{htmlInput: {"aria-label": heading}}}
                value={design?.id ?? ""}
                disabled={designs.length === 0}
                onChange={(event) => onSelect(event.target.value)}
                fullWidth
            >
                {designs.map((entry) => (
                    <MenuItem key={entry.id} value={entry.id}>
                        {entry.displayName}
                    </MenuItem>
                ))}
            </TextField>
            {design ? (
                <Box
                    ref={frame}
                    sx={{
                        position: "relative",
                        width: "100%",
                        aspectRatio: `${OVERLAY_WIDTH} / ${OVERLAY_HEIGHT}`,
                        overflow: "hidden",
                        borderRadius: 1,
                        border: 1,
                        borderColor: "divider",
                        bgcolor: "grey.900",
                    }}
                >
                    <Box
                        component="iframe"
                        // A new design is a new document.
                        key={layoutPath(design)}
                        title={i18n.t("commentary_preview")}
                        src={`${BASE_URL}${layoutPath(design)}`}
                        tabIndex={-1}
                        sx={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: OVERLAY_WIDTH,
                            height: OVERLAY_HEIGHT,
                            border: 0,
                            transform: `scale(${scale})`,
                            transformOrigin: "0 0",
                            pointerEvents: "none",
                        }}
                    />
                </Box>
            ) : (
                <Typography color="text.secondary">
                    {empty ?? i18n.t("commentary_no_design")}
                </Typography>
            )}
        </Stack>
    );
}
