import React from "react";
import {
    Alert,
    Button,
    Card,
    CardContent,
    Stack,
    TextField,
    Tooltip,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import {copyText} from "../PanelShell/clipboard";

// The Board page's cards beside the colours (ADR-005 S4s): opening the Designer
// and how to add the scoreboard and the winner popup to OBS, as local files (the
// Qt panel's groups).

// GET /api/v1/layouts/selected/board/obs-path. `design` is the file of the design
// `designId`, the one selected when the server answered; both null without one.
export interface BoardFiles {
    designId: string | null;
    design: string | null;
    winnerPopup: string;
}

export type BoardFile = "design" | "winnerPopup";

// Exactly what the desktop's view keeps to itself and opens its Designer window
// for, rather than handing it to the default browser
// (_IsDesignerUrl in src/AutonaWebPanelHost.py). In a browser it is the
// Designer's page, which says to open it from the desktop app.
const DESIGNER_URL = "/web?view=designer";

export function DesignerCard() {
    const title = i18n.t("board_designer_title");
    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("board_designer_description")}
                    </Typography>
                    <Tooltip title={i18n.t("board_designer_tooltip")}
                             describeChild>
                        <Button
                            variant="outlined"
                            size="small"
                            onClick={() => window.open(
                                DESIGNER_URL, "_blank", "noopener")}
                        >
                            {i18n.t("board_designer_open")}
                        </Button>
                    </Tooltip>
                </Stack>
            </CardContent>
        </Card>
    );
}

// A path with its copy and "show in Explorer". The field selects itself on
// focus: on a page that may not copy, the operator copies from it by hand.
function FileRow({label, path, target, reveal, revealing}: {
    label: string;
    path: string | null;
    target: BoardFile;
    reveal: (target: BoardFile) => void;
    revealing: boolean;
}) {
    const copy = async () => {
        if (path && await copyText(path)) {
            showSnackbar(i18n.t("commentary_obs_copied"), "success");
        } else {
            showSnackbar(i18n.t("commentary_obs_copy_failed"), "error");
        }
    };

    return (
        <Stack spacing={1}>
            <TextField
                size="small"
                value={path ?? ""}
                slotProps={{htmlInput: {readOnly: true, "aria-label": label}}}
                onFocus={(event) => event.target.select()}
                fullWidth
            />
            <Stack direction="row" spacing={1}>
                <Button
                    variant="contained"
                    size="small"
                    disabled={!path}
                    onClick={copy}
                    aria-label={`${label}: ${i18n.t("commentary_obs_copy")}`}
                >
                    {i18n.t("commentary_obs_copy")}
                </Button>
                <Button
                    variant="outlined"
                    size="small"
                    disabled={!path || revealing}
                    onClick={() => reveal(target)}
                    aria-label={`${label}: ${i18n.t("board_obs_reveal")}`}
                >
                    {i18n.t("board_obs_reveal")}
                </Button>
            </Stack>
        </Stack>
    );
}

export function ObsFilesCard({files, notice, reveal, revealing}: {
    files: Pick<BoardFiles, "design" | "winnerPopup"> | null;
    // Why there are no paths: another computer without the token.
    notice: string | null;
    reveal: (target: BoardFile) => void;
    // A window is on its way: another press would open another.
    revealing: boolean;
}) {
    const title = i18n.t("board_obs_title");
    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    {[1, 2, 3].map((step) => (
                        <Typography key={step} variant="body2">
                            {`${step}. ${i18n.t(`board_obs_step_${step}`)}`}
                        </Typography>
                    ))}
                    {notice && <Alert severity="info">{notice}</Alert>}
                    <FileRow
                        label={i18n.t("board_obs_design_path")}
                        path={files?.design ?? null}
                        target="design"
                        reveal={reveal}
                        revealing={revealing}
                    />
                    <Typography variant="body2">
                        {i18n.t("board_obs_popup_title")}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("board_obs_popup_hint")}
                    </Typography>
                    <FileRow
                        label={i18n.t("board_obs_popup_path")}
                        path={files?.winnerPopup ?? null}
                        target="winnerPopup"
                        reveal={reveal}
                        revealing={revealing}
                    />
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("board_obs_note")}
                    </Typography>
                </Stack>
            </CardContent>
        </Card>
    );
}
