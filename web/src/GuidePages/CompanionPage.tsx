import React from "react";
import {
    Alert,
    Box,
    ButtonBase,
    Stack,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Tooltip,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {canCopyImage, copyImage} from "../PanelShell/clipboard";
import ConnectionCard, {LanState, guideBaseUrl, shownUrl} from "./ConnectionCard";
import {useConnection} from "./useConnection";
import {Example, examplesOf} from "./examples";
import {spacedJson} from "./format";
import {ICON_SIZE, drawKeyIcon} from "./keyIcon";
import {CardFrame, Code, CopyIconButton, GuideFrame, Steps} from "./parts";

// The Companion guide (ADR-005 S-final SF-4a-2), /web/companion: the Qt
// Companion settings window as a page -- the connection, the setup steps,
// the example button mappings (examples.json) with their URL and body to
// copy, and the button icons to copy onto Companion's keys.

const STEPS = 6;

// The icons' size on the page (the window's: half the drawn size).
const ICON_SHOWN = ICON_SIZE / 2;

function companionLanNote(state: LanState): string {
    return i18n.t(`guide_companion_lan_${state}`);
}

export function exampleLabel(row: Example): string {
    return i18n.t(`guide_example_${row.id}`);
}

// Narrow cells: the table fits the app's view (~860 px) without scrolling.
const CELL_SX = {px: 1} as const;

function Mappings({rows, baseUrl}: {rows: Example[]; baseUrl: string | null}) {
    return (
        <CardFrame title={i18n.t("guide_companion_mappings")}>
            <TableContainer>
                <Table size="small"
                       aria-label={i18n.t("guide_companion_mappings")}>
                    <TableHead>
                        <TableRow>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_operation")}</TableCell>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_method")}</TableCell>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_url")}</TableCell>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_body")}</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {rows.map((row) => {
                            const label = exampleLabel(row);
                            const url = shownUrl(baseUrl, row.path);
                            const body = row.body ? spacedJson(row.body) : "";
                            return (
                                <TableRow key={row.id}>
                                    <TableCell sx={CELL_SX}>{label}</TableCell>
                                    <TableCell sx={CELL_SX}>{row.method}</TableCell>
                                    <TableCell sx={CELL_SX}>
                                        <Stack direction="row" spacing={0.5}
                                               alignItems="center">
                                            <Box sx={{flex: 1, minWidth: 0}}>
                                                <Code>{url}</Code>
                                            </Box>
                                            <CopyIconButton
                                                text={baseUrl === null ? "" : url}
                                                name={`${i18n.t("guide_copy_url")}: ${label}`}/>
                                        </Stack>
                                    </TableCell>
                                    <TableCell sx={{...CELL_SX, whiteSpace: "nowrap"}}>
                                        {body ? (
                                            <Stack direction="row" spacing={0.5}
                                                   alignItems="center">
                                                <Code>{body}</Code>
                                                <CopyIconButton
                                                    text={body}
                                                    name={`${i18n.t("guide_copy_body")}: ${label}`}/>
                                            </Stack>
                                        ) : "—"}
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </TableContainer>
        </CardFrame>
    );
}

function drawAll(rows: Example[]) {
    return rows.map((row) => ({row, url: drawKeyIcon(row.icon)}));
}

function KeyIcons({rows}: {rows: Example[]}) {
    // Drawn at once, and again once the page's fonts are loaded: a canvas
    // draws with whatever font is there when it draws (the labels in the
    // icons are not translated, so nothing else redraws them).
    const [icons, setIcons] = React.useState(() => drawAll(rows));
    React.useEffect(() => {
        let live = true;
        const fonts = (document as {fonts?: FontFaceSet}).fonts;
        fonts?.ready.then(() => {
            if (live) {
                setIcons(drawAll(rows));
            }
        }, () => undefined);
        return () => {
            live = false;
        };
    }, [rows]);
    const copyable = canCopyImage();

    const copy = (row: Example, url: string) => {
        copyImage(url).then(
            () => showSnackbar(i18n.t("guide_companion_icon_copied",
                                      {name: exampleLabel(row)}), "success"),
            (error: unknown) => {
                console.error(error);
                showSnackbar(i18n.t("guide_copy_failed"), "error");
            });
    };

    return (
        <CardFrame title={i18n.t("guide_companion_icons")}>
            {copyable ? (
                <Typography variant="body2">
                    {i18n.t("guide_companion_icons_hint")}
                </Typography>
            ) : (
                // A page from another computer over http has no image
                // clipboard: the image is still there to save.
                <Alert severity="info">
                    {i18n.t("guide_companion_icons_no_copy")}
                </Alert>
            )}
            <Box sx={{display: "grid", gap: 2,
                      gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))"}}>
                {icons.map(({row, url}) => {
                    const label = exampleLabel(row);
                    const picture = url ? (
                        <img src={url} alt={label} width={ICON_SHOWN}
                             height={ICON_SHOWN} style={{display: "block"}}/>
                    ) : (
                        <Box aria-label={label} sx={{width: ICON_SHOWN,
                                                     height: ICON_SHOWN,
                                                     bgcolor: row.icon.bg}}/>
                    );
                    return (
                        <Stack key={row.id} alignItems="center" spacing={0.5}>
                            {copyable && url ? (
                                <Tooltip title={i18n.t("guide_companion_copy_icon")}>
                                    <ButtonBase
                                        aria-label={`${i18n.t("guide_companion_copy_icon")}: ${label}`}
                                        onClick={() => copy(row, url)}
                                        sx={{borderRadius: 1}}>
                                        {picture}
                                    </ButtonBase>
                                </Tooltip>
                            ) : picture}
                            <Typography variant="caption" align="center">
                                {label}
                            </Typography>
                        </Stack>
                    );
                })}
            </Box>
        </CardFrame>
    );
}

function CompanionContent() {
    const {connection, status, refresh} = useConnection();
    const rows = React.useMemo(() => examplesOf("companion"), []);
    const baseUrl = guideBaseUrl(connection, status, "baseUrl");
    const steps = Array.from({length: STEPS}, (_value, index) =>
        i18n.t(`guide_companion_step_${index + 1}`));

    return (
        <GuideFrame panel="companion" title={i18n.t("guide_companion_title")}
                    description={i18n.t("guide_companion_description")}>
            <ConnectionCard connection={connection} status={status}
                            refresh={refresh} url={baseUrl}
                            lanNote={companionLanNote}/>
            <CardFrame title={i18n.t("guide_setup_steps")}>
                <Steps steps={steps}/>
            </CardFrame>
            <Mappings rows={rows} baseUrl={baseUrl}/>
            <KeyIcons rows={rows}/>
        </GuideFrame>
    );
}

export default function CompanionPage() {
    return (
        <FeedbackProvider>
            <CompanionContent/>
        </FeedbackProvider>
    );
}
