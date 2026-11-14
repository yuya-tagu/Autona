import React from "react";
import {
    Box,
    Stack,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {FeedbackProvider} from "../ScoreboardPage/Feedback";
import ConnectionCard, {LanState, guideBaseUrl, shownUrl} from "./ConnectionCard";
import {Connection, ConnectionStatus, useConnection} from "./useConnection";
import {Example, examplesOf} from "./examples";
import {compactJson, watchPath} from "./format";
import {
    CardFrame,
    Code,
    CopyButton,
    CopyIconButton,
    GuideFrame,
    Note,
    Steps,
} from "./parts";

// The OBS guide (ADR-005 S-final SF-4a-2), /web/obs: the Qt OBS / Advanced
// Scene Switcher window as a page -- the connection (this PC's loopback:
// OBS runs here) and out/, the HTTP actions that control Autona
// (examples.json) and the files whose changes trigger OBS, as absolute
// paths with this PC's separator.

const HTTP_STEPS = 5;
const WATCH_STEPS = 4;

// The files a macro watches (below out/, "/" between the parts) and the
// translation of what each holds (guide_obs_watch_<key>).
export const WATCH_FILES = [
    {key: "program_state", path: "out/program_state.json"},
    {key: "team1_score", path: "out/score/1/team/1/score.txt"},
    {key: "team2_score", path: "out/score/1/team/2/score.txt"},
    {key: "team1_tag", path: "out/score/1/team/1/player/1/gamerTag.txt"},
    {key: "team2_tag", path: "out/score/1/team/2/player/1/gamerTag.txt"},
    {key: "match", path: "out/score/1/match.txt"},
    {key: "phase", path: "out/score/1/phase.txt"},
    {key: "best_of", path: "out/score/1/best_of.txt"},
];

function obsLanNote(state: LanState): string {
    return i18n.t(`guide_obs_lan_${state}`);
}

function stepsOf(prefix: string, count: number): string[] {
    return Array.from({length: count}, (_value, index) =>
        i18n.t(`${prefix}_${index + 1}`));
}

// Narrow cells: the tables fit the app's view (~860 px) without scrolling.
const CELL_SX = {px: 1} as const;

function HttpActions({rows, baseUrl}: {rows: Example[];
                                       baseUrl: string | null}) {
    return (
        <CardFrame title={i18n.t("guide_obs_http_title")}>
            <Steps steps={stepsOf("guide_obs_http_step", HTTP_STEPS)}/>
            <Note>※ {i18n.t("guide_obs_http_version_note")}</Note>
            <TableContainer>
                <Table size="small" aria-label={i18n.t("guide_obs_http_title")}>
                    <TableHead>
                        <TableRow>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_operation")}</TableCell>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_url")}</TableCell>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_body_json")}</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {rows.map((row) => {
                            const label = i18n.t(`guide_example_${row.id}`);
                            const url = shownUrl(baseUrl, row.path);
                            const body = compactJson(row.body);
                            return (
                                <TableRow key={row.id}>
                                    <TableCell sx={CELL_SX}>{label}</TableCell>
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
            <Note>{i18n.t("guide_obs_destructive_note")}</Note>
        </CardFrame>
    );
}

function WatchFiles({connection, status}: {connection: Connection | null;
                                            status: ConnectionStatus}) {
    return (
        <CardFrame title={i18n.t("guide_obs_watch_title")}>
            <Steps steps={stepsOf("guide_obs_watch_step", WATCH_STEPS)}/>
            <Note>※ {i18n.t("guide_obs_export_note")}</Note>
            {status === "thisPcOnly" ? (
                <Note>{i18n.t("guide_obs_paths_this_pc_only")}</Note>
            ) : null}
            <TableContainer>
                <Table size="small" aria-label={i18n.t("guide_obs_watch_title")}>
                    <TableHead>
                        <TableRow>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_obs_watch_file")}</TableCell>
                            <TableCell sx={CELL_SX}>{i18n.t("guide_obs_contains")}</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {WATCH_FILES.map((file) => {
                            // Absolute on this PC; the path below the app's
                            // folder where the connection is not this
                            // page's to read (another computer).
                            const path = connection
                                ? watchPath(connection.outDir,
                                            connection.pathSeparator,
                                            file.path)
                                : file.path;
                            const contains = i18n.t(`guide_obs_watch_${file.key}`);
                            return (
                                <TableRow key={file.key}>
                                    <TableCell sx={CELL_SX}>
                                        <Stack direction="row" spacing={0.5}
                                               alignItems="center">
                                            <Box sx={{flex: 1, minWidth: 0}}>
                                                <Code>{path}</Code>
                                            </Box>
                                            {connection ? (
                                                <CopyIconButton
                                                    text={path}
                                                    name={`${i18n.t("guide_obs_copy_path")}: ${contains}`}/>
                                            ) : null}
                                        </Stack>
                                    </TableCell>
                                    <TableCell sx={CELL_SX}>{contains}</TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </TableContainer>
            <Note>{i18n.t("guide_obs_watch_note")}</Note>
        </CardFrame>
    );
}

function ObsContent() {
    const {connection, status, refresh} = useConnection();
    const rows = React.useMemo(() => examplesOf("obs"), []);
    const baseUrl = guideBaseUrl(connection, status, "localBaseUrl");

    return (
        <GuideFrame panel="obs" title={i18n.t("guide_obs_title")}
                    description={i18n.t("guide_obs_description")}>
            <ConnectionCard connection={connection} status={status}
                            refresh={refresh} url={baseUrl}
                            lanNote={obsLanNote}>
                {connection ? (
                    <Stack direction="row" spacing={1} alignItems="center"
                           flexWrap="wrap" useFlexGap>
                        <Typography variant="body2">
                            {i18n.t("guide_obs_out_dir")}:
                        </Typography>
                        <Box sx={{flex: 1, minWidth: 200}}>
                            <Code>{connection.outDir}</Code>
                        </Box>
                        <CopyButton text={connection.outDir}
                                    name={i18n.t("guide_obs_copy_out_dir")}/>
                    </Stack>
                ) : null}
            </ConnectionCard>
            <HttpActions rows={rows} baseUrl={baseUrl}/>
            <WatchFiles connection={connection} status={status}/>
        </GuideFrame>
    );
}

export default function ObsPage() {
    return (
        <FeedbackProvider>
            <ObsContent/>
        </FeedbackProvider>
    );
}
