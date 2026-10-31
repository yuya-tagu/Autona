import React from "react";
import {
    Box,
    Button,
    Card,
    CardContent,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {BACKEND_PORT} from "../env";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import {useStateChoice} from "../PanelShell/useStateChoice";

// The Commentary page's cards below the casters (ADR-005 S4b): the
// feature's own colours (src/AutonaFeatureColors.py's card) and how to add
// the overlay to OBS (the Qt panel's group).

// A catalog entry (GET /api/v1/layouts, core/LayoutCatalog.py).
export interface LayoutEntry {
    id: string;
    kind: string;
    displayName: string;
    entry: string;
}

export type ColorKind = "theme" | "accent";

// <input type="color"> takes #rrggbb only; the state may hold #rgb.
function sixDigits(color: string): string {
    const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(color);
    if (short) {
        return `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}`;
    }
    return /^#[0-9a-f]{6}$/i.test(color) ? color : "#000000";
}

function ColorRow({label, value, send}: {
    label: string;
    value: string;
    send: (color: string) => unknown;
}) {
    const [color, choose] = useStateChoice(value, send);
    // What the picker shows while it is open: sent once it is closed (the
    // input's `change`, not each `input` while dragging).
    const [draft, setDraft] = React.useState<string | null>(null);
    const input = React.useRef<HTMLInputElement>(null);
    const chooseRef = React.useRef(choose);
    chooseRef.current = choose;

    React.useEffect(() => {
        const element = input.current;
        if (!element) {
            return undefined;
        }
        const onChange = () => {
            setDraft(null);
            chooseRef.current(element.value);
        };
        element.addEventListener("change", onChange);
        return () => element.removeEventListener("change", onChange);
    }, []);

    return (
        <Stack direction="row" alignItems="center" spacing={1}>
            <Typography variant="body2" sx={{flex: 1}}>{label}</Typography>
            <Box
                component="input"
                type="color"
                ref={input}
                aria-label={label}
                value={draft ?? sixDigits(color)}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
                    // The picker moving (`input`); its `change` is the send
                    // above.
                    if (event.nativeEvent.type !== "change") {
                        setDraft(event.target.value);
                    }
                }}
                sx={{width: 48, height: 28, p: 0, border: 0,
                     bgcolor: "transparent", cursor: "pointer"}}
            />
        </Stack>
    );
}

export function FeatureColorCard({title, theme, accent, tournament, send}: {
    title: string;
    theme: string;
    accent: string;
    // The tournament's (theme, accent): what "match the tournament" sets.
    // Null until the state has arrived.
    tournament: {theme: string; accent: string} | null;
    send: (colors: Partial<Record<ColorKind, string>>) => Promise<unknown>;
}) {
    const sendTheme = React.useCallback(
        (color: string) => send({theme: color}), [send]);
    const sendAccent = React.useCallback(
        (color: string) => send({accent: color}), [send]);

    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    <ColorRow
                        label={i18n.t("commentary_color_theme")}
                        value={theme}
                        send={sendTheme}
                    />
                    <ColorRow
                        label={i18n.t("commentary_color_accent")}
                        value={accent}
                        send={sendAccent}
                    />
                    {/* Both written explicitly, as the Qt card does: the
                        feature keeps them when the tournament's change. */}
                    <Button
                        variant="outlined"
                        size="small"
                        disabled={!tournament}
                        onClick={() => tournament
                            && send(tournament).catch(() => undefined)}
                    >
                        {i18n.t("commentary_color_match_tournament")}
                    </Button>
                </Stack>
            </CardContent>
        </Card>
    );
}

// The overlay's URL for OBS on this PC: localhost and the server's port,
// whatever host this page was opened from (the Qt panel's ObsUrl).
export function obsUrl(design: LayoutEntry): string {
    return `http://localhost:${BACKEND_PORT}/layout/${design.id}/${design.entry}`;
}

async function copyText(text: string): Promise<boolean> {
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch {
        // Not allowed here (a page from another computer over http, the
        // desktop's view): the copy command below still is, on a click.
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try {
        copied = document.execCommand("copy");
    } catch {
        copied = false;
    }
    document.body.removeChild(area);
    return copied;
}

export function ObsUrlCard({design}: {design: LayoutEntry | null}) {
    const url = design ? obsUrl(design) : "";
    const title = i18n.t("commentary_obs_title");

    const copy = async () => {
        if (await copyText(url)) {
            showSnackbar(i18n.t("commentary_obs_copied"), "success");
        } else {
            showSnackbar(i18n.t("commentary_obs_copy_failed"), "error");
        }
    };

    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    {[1, 2, 3].map((step) => (
                        <Typography key={step} variant="body2">
                            {`${step}. ${i18n.t(`commentary_obs_step_${step}`)}`}
                        </Typography>
                    ))}
                    <Stack direction="row" spacing={1}>
                        <TextField
                            size="small"
                            value={url}
                            slotProps={{htmlInput: {
                                readOnly: true,
                                "aria-label": i18n.t("commentary_obs_url"),
                            }}}
                            onFocus={(event) => event.target.select()}
                            sx={{flex: 1, minWidth: 0}}
                        />
                        <Button
                            variant="contained"
                            size="small"
                            disabled={!design}
                            onClick={copy}
                        >
                            {i18n.t("commentary_obs_copy")}
                        </Button>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("commentary_obs_note")}
                    </Typography>
                </Stack>
            </CardContent>
        </Card>
    );
}
