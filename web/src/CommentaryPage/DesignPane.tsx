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
// The Top 8 page's too (S4i), with its own heading and empty text. The Board
// page's too (S4s), with what only it has: controls above the preview, a picture
// behind the overlay, a second overlay page on top, a way to load them again and
// a preview that also keeps to the height left for it.

const OVERLAY_WIDTH = 1920;
const OVERLAY_HEIGHT = 1080;
// The preview's border, on both sides: what `fit` leaves out of what it measures.
const FRAME_BORDERS = 2;

function layoutPath(design: LayoutEntry): string {
    const id = design.id.split("/").map(encodeURIComponent).join("/");
    return `/layout/${id}/${encodeURIComponent(design.entry)}`;
}

// What the select picks a design by: `id`, unless the entry has its own key.
function selectKey(design: LayoutEntry): string {
    return design.key ?? design.id;
}

function usePreviewScale(
    frame: React.RefObject<HTMLDivElement | null>,
    shown: boolean,
    fit: boolean,
): number {
    const [scale, setScale] = React.useState(0.25);
    React.useLayoutEffect(() => {
        const element = frame.current;
        if (!element) {
            return undefined;
        }
        const measure = () => {
            if (element.clientWidth > 0) {
                setScale(fit
                    ? Math.min(
                        (element.clientWidth - FRAME_BORDERS) / OVERLAY_WIDTH,
                        (element.clientHeight - FRAME_BORDERS) / OVERLAY_HEIGHT)
                    : element.clientWidth / OVERLAY_WIDTH);
            }
        };
        measure();
        if (typeof ResizeObserver === "undefined") {
            return undefined;
        }
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [frame, shown, fit]);
    return scale;
}

// The overlay pages lie on one another: one 1920x1080 frame each, scaled.
function frameSx(scale: number) {
    return {
        position: "absolute",
        top: 0,
        left: 0,
        width: OVERLAY_WIDTH,
        height: OVERLAY_HEIGHT,
        border: 0,
        transform: `scale(${scale})`,
        transformOrigin: "0 0",
        pointerEvents: "none",
    };
}

export default function DesignPane({
    designs, design, onSelect, label, empty, controls, background, overlay,
    reloadKey, fit = false,
}: {
    designs: LayoutEntry[];
    design: LayoutEntry | null;
    // Called with the design's select key (its `key`, else its `id`).
    onSelect: (key: string) => void;
    // The heading (and the select's name), and what shows without a
    // design: the Commentary page's by default.
    label?: string;
    empty?: string;
    // Between the select and the preview.
    controls?: React.ReactNode;
    // A picture of the server's behind the design (a path): what shows through
    // the overlay's transparent page.
    background?: string;
    // A page of the server's on top of the design (a path), with its frame's
    // name. Its fragment changing is no new document.
    overlay?: {src: string; title: string} | null;
    // A new value loads the frames again: a page the server has changed.
    reloadKey?: number;
    // Scale to the smaller of the width and the height the pane has left (the
    // Qt preview's rule), rather than to the width. For a pane in a column of a
    // set height, from the md breakpoint up; below it the preview is by width.
    fit?: boolean;
}) {
    const frame = React.useRef<HTMLDivElement>(null);
    const scale = usePreviewScale(frame, design !== null, fit);
    const heading = label ?? i18n.t("commentary_design");
    const shownKey = design ? selectKey(design) : "";

    const preview = design && (
        <Box
            ref={fit ? undefined : frame}
            sx={{
                position: "relative",
                ...(fit ? {
                    flexShrink: 0,
                    boxSizing: "content-box",
                    width: `${OVERLAY_WIDTH * scale}px`,
                    height: `${OVERLAY_HEIGHT * scale}px`,
                } : {
                    width: "100%",
                    aspectRatio: `${OVERLAY_WIDTH} / ${OVERLAY_HEIGHT}`,
                }),
                overflow: "hidden",
                borderRadius: 1,
                border: 1,
                borderColor: "divider",
                bgcolor: "grey.900",
                ...(background ? {
                    backgroundImage: `url("${BASE_URL}${background}")`,
                    backgroundSize: "100% 100%",
                } : {}),
            }}
        >
            <Box
                component="iframe"
                // A new design is a new document.
                key={`${layoutPath(design)}#${reloadKey ?? 0}`}
                title={i18n.t("commentary_preview")}
                src={`${BASE_URL}${layoutPath(design)}`}
                tabIndex={-1}
                sx={frameSx(scale)}
            />
            {overlay && (
                <Box
                    component="iframe"
                    key={`overlay#${reloadKey ?? 0}`}
                    title={overlay.title}
                    src={`${BASE_URL}${overlay.src}`}
                    tabIndex={-1}
                    sx={frameSx(scale)}
                />
            )}
        </Box>
    );

    return (
        <Stack spacing={1} sx={fit ? {height: {md: "100%"}} : undefined}>
            <Typography variant="subtitle1" component="h2">{heading}</Typography>
            <TextField
                select
                size="small"
                // The select's own name (no floating label: the heading).
                slotProps={{htmlInput: {"aria-label": heading}}}
                value={design ? selectKey(design) : ""}
                disabled={designs.length === 0}
                onChange={(event) => onSelect(event.target.value)}
                fullWidth
            >
                {designs.map((entry) => {
                    const key = selectKey(entry);
                    return (
                        <MenuItem
                            key={key}
                            value={key}
                            // The select reports no pick of the value it holds,
                            // and what it shows may be the fallback for a kept
                            // design that is gone: that pick is the operator's
                            // choice as well. The server writes only what is
                            // not kept already.
                            onClick={key === shownKey
                                ? () => onSelect(key) : undefined}
                        >
                            {entry.displayName}
                        </MenuItem>
                    );
                })}
            </TextField>
            {controls}
            {preview && fit ? (
                <Box
                    ref={frame}
                    sx={{
                        // The height that is left, up from the md breakpoint;
                        // below it the column has no height to share out.
                        flex: {md: "1 1 0"},
                        minHeight: {md: 200},
                        aspectRatio: {
                            xs: `${OVERLAY_WIDTH} / ${OVERLAY_HEIGHT}`,
                            md: "auto",
                        },
                        width: "100%",
                        display: "flex",
                        justifyContent: "center",
                        alignItems: "flex-start",
                    }}
                >
                    {preview}
                </Box>
            ) : preview || (
                <Typography color="text.secondary">
                    {empty ?? i18n.t("commentary_no_design")}
                </Typography>
            )}
        </Stack>
    );
}
