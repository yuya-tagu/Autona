import React from "react";
import {useSelector} from "react-redux";
import {
    Alert,
    Box,
    Button,
    Card,
    CardContent,
    Chip,
    CircularProgress,
    IconButton,
    MenuItem,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import {ArrowDownward, ArrowUpward, DragIndicator} from "@mui/icons-material";
import i18n from "../i18n/config";
import {BASE_URL} from "../env";
import {ReduxState} from "../redux/store";
import {usePanelShell} from "../PanelShell/context";
import {useStateChoice} from "../PanelShell/useStateChoice";
import {canCopyImage} from "../PanelShell/clipboard";
import {ConfirmDialog} from "../StreamPage/Dialogs";
import {
    DropLine,
    dropIndex,
    movedOrder,
} from "../CommentaryPage";
import {
    CardFrame,
    Note,
    RangeField,
    generatedUrl,
} from "../ThumbnailPage/Cards";
import {CharacterSelects, StateText} from "../Top8Page/Cards";
import {
    CharacterPick,
    Characters,
    NO_PICK,
} from "../Top8Page/characterPick";

// The Result page's cards (ADR-005 S4r), the Qt 大会リザルト panel's groups: the
// layout with the standings' import and the clearing, the rows of a singles'
// or a team layout, and the preview with the making and the post on X. They
// draw what GET /api/v1/result answers and result_render from the state, and
// ask the page to write.

// core/ResultService.py's constants.
export const MAX_CHARACTERS = 3;
export const MAX_MEMBERS = 5;
export const RANK_RANGE: [number, number] = [1, 999];

const DRAG_TYPE = "application/x-autona-result-row";
const RANK_WIDTH = 76;
const SPONSOR_WIDTH = 96;

export type RowKind = "players" | "teams";

// A character as GET /api/v1/result reads it: its identifiers.
export interface ResultPick {
    en_name: string;
    skin: number;
}

// One of GET /api/v1/result's layouts. The name is the server's (translated by
// the app); the page's own words first.
export interface ResultLayout {
    id: string;
    name: string;
    width: number;
    height: number;
    // How many rows it draws.
    slots: number;
    team: boolean;
}

export interface ResultPlayerRow {
    slot: number;
    rank: number;
    name: string;
    sponsor: string;
    twitter: string;
    characters: (ResultPick | null)[];
    // The name of the character the scoreboard lends a player without one,
    // when it is drawn ("" for a character without a name); null: none.
    fromScoreboard: string | null;
}

export interface ResultMember {
    name: string;
    twitter: string;
    character: ResultPick | null;
    fromScoreboard: string | null;
}

export interface ResultTeamRow {
    slot: number;
    rank: number;
    name: string;
    members: ResultMember[];
}

// What PATCH /api/v1/result/players/<n> and /teams/<n> take. A character goes
// with the codename of the game whose list it was picked on: the server refuses
// it (409 CONFLICT, nothing written) when that is not the game loaded any more.
// Only what was edited goes, by its place ("1" the first): the rest of a row may
// not be what the page was shown (a character of another game reads as none).
export type WirePick = {en_name: string; skin: number; game: string} | null;

export interface PlayerFields {
    rank?: number;
    name?: string;
    sponsor?: string;
    twitter?: string;
    characters?: Record<string, WirePick>;
}

export interface TeamFields {
    rank?: number;
    name?: string;
    members?: Record<string, {name?: string; twitter?: string;
                              character?: WirePick}>;
}

// result_render: the render lane's last run (core ResultService).
export interface ResultRender {
    status: "idle" | "running" | "done" | "error";
    path: string;
    error: string;
    revision: number;
}

// What a generation of this page's came to (the status line).
export type GenerateResult =
    | {status: "done"; path: string}
    | {status: "error"; error: string};

// What the post's text is: read, or not to be read without the access token
// (it has handles from the player database), or not read yet, or not read after
// trying again.
export type PostState = "loading" | "ready" | "token" | "failed";

const RENDER_ERRORS = ["no_data", "payload_failed", "render_failed",
                       "save_failed", "cancelled"];

// result_render.error as words: a code core knows, else the render's failure.
export function errorText(code: string): string {
    return i18n.t(`result_error_${RENDER_ERRORS.includes(code)
        ? code : "render_failed"}`);
}

const IMPORT_ERRORS = ["fetch_failed", "no_standings", "changed",
                       "import_failed"];

// result_standings.error as words.
export function importErrorText(code: string): string {
    return i18n.t(`result_import_error_${IMPORT_ERRORS.includes(code)
        ? code : "fetch_failed"}`);
}

// The newest render as a PNG (GET /api/v1/result/preview). `v` is the run's
// revision: what the server answers is always the newest, and the revision
// tells the image cache one run from another.
export function previewUrl(revision: number): string {
    return `${BASE_URL}/api/v1/result/preview?v=${revision}`;
}

function toWire(pick: ResultPick | null, game: string): WirePick {
    // -1: a character without skins, which core takes with any row.
    return pick ? {en_name: pick.en_name, skin: Math.max(pick.skin, 0), game}
        : null;
}

function toPick([enName, row]: CharacterPick): ResultPick | null {
    return enName === null ? null : {en_name: enName, skin: row};
}

function fromPick(pick: ResultPick | null): CharacterPick {
    return pick ? [pick.en_name, pick.skin] : NO_PICK;
}

// A row's text as the state has it (null: the state has no such row).
function stateText(state: ReduxState, kind: RowKind, slot: number,
                   place: number | null, field: string): string | null {
    const data = state.autonaState.autonaState as unknown as
        Record<string, Record<string, Record<string, unknown>> | undefined>;
    const row = data[`result_${kind}`]?.[String(slot)];
    if (!row || typeof row !== "object") {
        return null;
    }
    const members = row.members;
    const holder = place === null ? row
        : (Array.isArray(members) ? members[place] : undefined) as
            Record<string, unknown> | undefined;
    const text = holder?.[field];
    return typeof text === "string" ? text : "";
}

// A text field of a row, showing what the state has as every other page's does.
// Not what the page read: the echo of what was typed would then come by a read,
// which may take longer than useStateField waits for it, and be taken for
// another writer's over what is being typed. `read` is for a row the state does
// not have yet.
function RowText({label, kind, slot, place = null, field, read, send, readOnly,
                  width}: {
    label: string;
    kind: RowKind;
    slot: number;
    // A team's member, from 0.
    place?: number | null;
    field: string;
    read: string;
    send: (value: string) => unknown;
    readOnly: boolean;
    width?: number;
}) {
    const value = useSelector((state: ReduxState) =>
        stateText(state, kind, slot, place, field));
    return (
        <StateText label={label} value={value ?? read} send={send}
                   readOnly={readOnly} width={width}/>
    );
}

function FromScoreboard({name}: {name: string | null}) {
    if (name === null) {
        return null;
    }
    const said = i18n.t("result_from_scoreboard");
    return (
        <Typography variant="caption" color="text.secondary" component="p"
                    sx={{m: 0}}>
            {name ? `${said}: ${name}` : said}
        </Typography>
    );
}

// ── レイアウト ───────────────────────────────────────────────────────────

export function LayoutCard({layout, layouts, game, send, importing, importNote,
                            loadStandings, kind, clear}: {
    layout: string;
    layouts: ResultLayout[];
    game: {codename: string; name: string} | null;
    send: (layout: string) => Promise<unknown>;
    // The standings are on their way (from any client).
    importing: boolean;
    // What the last import of this page's came to.
    importNote: {severity: "success" | "error" | "info"; text: string} | null;
    loadStandings: () => void;
    kind: RowKind;
    clear: () => void;
}) {
    const [shown, choose] = useStateChoice(layout, send);
    const [confirming, setConfirming] = React.useState(false);
    return (
        <CardFrame title={i18n.t("result_layout")}>
            <TextField
                select
                size="small"
                label={i18n.t("result_layout_field")}
                value={layouts.some((entry) => entry.id === shown) ? shown : ""}
                onChange={(event) => choose(event.target.value)}
            >
                {layouts.map((entry) => (
                    <MenuItem key={entry.id} value={entry.id}>
                        {i18n.t(`result_layout_${entry.id}`,
                                {defaultValue: entry.name})}
                    </MenuItem>
                ))}
            </TextField>
            {!game && <Note>{i18n.t("result_no_game")}</Note>}
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap"
                   alignItems="center">
                <Button variant="contained" disabled={importing}
                        onClick={loadStandings}>
                    {i18n.t("result_import")}
                </Button>
                <Button variant="outlined" color="error" disabled={importing}
                        onClick={() => setConfirming(true)}>
                    {i18n.t("result_clear")}
                </Button>
            </Stack>
            {importing && (
                <Stack direction="row" spacing={1} alignItems="center"
                       role="status">
                    <CircularProgress size={16}/>
                    <Typography variant="body2" color="text.secondary">
                        {i18n.t("result_importing")}
                    </Typography>
                </Stack>
            )}
            {!importing && importNote && (
                <Alert severity={importNote.severity} role="status">
                    {importNote.text}
                </Alert>
            )}
            <ConfirmDialog
                open={confirming}
                title={i18n.t("result_clear")}
                message={i18n.t(`result_clear_confirm_${kind}`)}
                confirm={i18n.t("result_clear_yes")}
                onConfirm={() => {
                    setConfirming(false);
                    clear();
                }}
                onClose={() => setConfirming(false)}
            />
        </CardFrame>
    );
}

// ── 行 ───────────────────────────────────────────────────────────────────

interface RowDrag {
    onGripStart: (index: number, event: React.DragEvent<HTMLElement>) => void;
    onEnd: () => void;
    onOver: (index: number, event: React.DragEvent<HTMLElement>) => void;
    onDrop: (index: number, event: React.DragEvent<HTMLElement>) => void;
}

// One row's frame: its way to another place, by the buttons (the keyboard's)
// or by the grip (the mouse's, the Commentary page's drag), around its fields.
function RowFrame({slot, index, count, label, move, drag, importing,
                   children}: {
    slot: number;
    index: number;
    count: number;
    label: string;
    move: (index: number, delta: -1 | 1) => void;
    drag: RowDrag;
    importing: boolean;
    children: React.ReactNode;
}) {
    return (
        <Card
            variant="outlined"
            component="section"
            aria-label={label}
            data-result-row={slot}
            onDragOver={(event) => drag.onOver(index, event)}
            onDrop={(event) => drag.onDrop(index, event)}
            sx={{display: "flex", gap: 0.5, p: 1, pl: 0.5}}
        >
            <Stack alignItems="center" sx={{flexShrink: 0}}>
                <IconButton
                    size="small"
                    data-move="up"
                    aria-label={i18n.t("result_move_up", {row: label})}
                    title={i18n.t("result_move_up", {row: label})}
                    disabled={index === 0 || importing}
                    onClick={() => move(index, -1)}
                >
                    <ArrowUpward fontSize="small"/>
                </IconButton>
                <Box
                    component="span"
                    draggable={!importing}
                    aria-hidden
                    title={i18n.t("result_drag")}
                    onDragStart={(event) => drag.onGripStart(index, event)}
                    onDragEnd={drag.onEnd}
                    sx={{display: "flex", cursor: "grab",
                         color: "text.secondary"}}
                >
                    <DragIndicator fontSize="small"/>
                </Box>
                <IconButton
                    size="small"
                    data-move="down"
                    aria-label={i18n.t("result_move_down", {row: label})}
                    title={i18n.t("result_move_down", {row: label})}
                    disabled={index === count - 1 || importing}
                    onClick={() => move(index, 1)}
                >
                    <ArrowDownward fontSize="small"/>
                </IconButton>
            </Stack>
            <Stack spacing={1} sx={{flex: 1, minWidth: 0}}>
                {children}
            </Stack>
        </Card>
    );
}

interface RowProps<Row, Fields> {
    row: Row;
    index: number;
    count: number;
    characters: Characters | null;
    // The codename of the game `characters` is the list of.
    game: string;
    send: (slot: number, fields: Fields) => Promise<unknown>;
    move: (index: number, delta: -1 | 1) => void;
    drag: RowDrag;
    // Its fields are not to be edited: this card may not hold this row any
    // more (a move not read yet), or the standings are about to replace it.
    locked: boolean;
    importing: boolean;
}

function PlayerRowCard({row, index, count, characters, game, send, move, drag,
                        locked, importing}: RowProps<ResultPlayerRow,
                                                     PlayerFields>) {
    // The shell's language: a memoized card still re-renders its labels when
    // it changes.
    usePanelShell();
    const slot = row.slot;
    const sendRank = React.useCallback(
        (rank: number) => send(slot, {rank}), [slot, send]);
    const sendName = React.useCallback(
        (name: string) => send(slot, {name}), [slot, send]);
    const sendSponsor = React.useCallback(
        (sponsor: string) => send(slot, {sponsor}), [slot, send]);
    const sendTwitter = React.useCallback(
        (twitter: string) => send(slot, {twitter}), [slot, send]);

    return (
        <RowFrame slot={slot} index={index} count={count}
                  label={i18n.t("result_row", {slot})} move={move} drag={drag}
                  importing={importing}>
            {/* Sponsor left of the name ("[Sponsor] Name"), as the scoreboard
                and the results show it. */}
            <Stack direction="row" spacing={1}>
                <RangeField label={i18n.t("result_rank")} value={row.rank}
                            range={RANK_RANGE} send={sendRank} percent={false}
                            width={RANK_WIDTH} step={1} readOnly={locked}/>
                <RowText label={i18n.t("result_sponsor")} kind="players"
                         slot={slot} field="sponsor" read={row.sponsor}
                         send={sendSponsor} readOnly={locked}
                         width={SPONSOR_WIDTH}/>
                <RowText label={i18n.t("result_name")} kind="players"
                         slot={slot} field="name" read={row.name}
                         send={sendName} readOnly={locked}/>
            </Stack>
            <Stack direction="row">
                <RowText label={i18n.t("result_twitter")} kind="players"
                         slot={slot} field="twitter" read={row.twitter}
                         send={sendTwitter} readOnly={locked}/>
            </Stack>
            {Array.from({length: MAX_CHARACTERS}, (_unused, place) => (
                <CharacterSelects
                    key={place}
                    pick={fromPick(row.characters[place] ?? null)}
                    characters={characters}
                    characterLabel={i18n.t(`result_character_${place + 1}`)}
                    colorLabel={i18n.t("result_color", {number: place + 1})}
                    readOnly={locked}
                    send={(pick) => send(slot, {characters: {
                        [place + 1]: toWire(toPick(pick), game)}})}
                />
            ))}
            <FromScoreboard name={row.fromScoreboard}/>
        </RowFrame>
    );
}

const MemoPlayerRow = React.memo(PlayerRowCard);

const EMPTY_MEMBER: ResultMember = {name: "", twitter: "", character: null,
                                    fromScoreboard: null};

function MemberFields({slot, place, member, characters, game, send, locked}: {
    slot: number;
    place: number;
    member: ResultMember;
    characters: Characters | null;
    game: string;
    send: (slot: number, fields: TeamFields) => Promise<unknown>;
    locked: boolean;
}) {
    const edit = (fields: NonNullable<TeamFields["members"]>[string]) =>
        send(slot, {members: {[place + 1]: fields}});
    return (
        <Stack spacing={1} role="group"
               aria-label={i18n.t("result_member", {number: place + 1})}>
            <Typography variant="caption" color="text.secondary"
                        component="p" sx={{m: 0}}>
                {i18n.t("result_member", {number: place + 1})}
            </Typography>
            <Stack direction="row" spacing={1}>
                <RowText label={i18n.t("result_name")} kind="teams" slot={slot}
                         place={place} field="name" read={member.name}
                         send={(name) => edit({name})} readOnly={locked}/>
                <RowText label={i18n.t("result_twitter")} kind="teams"
                         slot={slot} place={place} field="twitter"
                         read={member.twitter}
                         send={(twitter) => edit({twitter})} readOnly={locked}/>
            </Stack>
            <CharacterSelects
                pick={fromPick(member.character)}
                characters={characters}
                readOnly={locked}
                send={(pick) => edit({
                    character: toWire(toPick(pick), game)})}
            />
            <FromScoreboard name={member.fromScoreboard}/>
        </Stack>
    );
}

function TeamRowCard({row, index, count, characters, game, send, move, drag,
                      locked, importing}: RowProps<ResultTeamRow, TeamFields>) {
    usePanelShell();
    const slot = row.slot;
    const sendRank = React.useCallback(
        (rank: number) => send(slot, {rank}), [slot, send]);
    const sendName = React.useCallback(
        (name: string) => send(slot, {name}), [slot, send]);
    // Five rows whatever the state holds: a member keeps its place.
    const members = React.useMemo(() => Array.from(
        {length: MAX_MEMBERS},
        (_unused, place) => row.members[place] ?? EMPTY_MEMBER), [row.members]);

    return (
        <RowFrame slot={slot} index={index} count={count}
                  label={i18n.t("result_team_row", {slot})} move={move}
                  drag={drag} importing={importing}>
            <Stack direction="row" spacing={1}>
                <RangeField label={i18n.t("result_rank")} value={row.rank}
                            range={RANK_RANGE} send={sendRank} percent={false}
                            width={RANK_WIDTH} step={1} readOnly={locked}/>
                <RowText label={i18n.t("result_team_name")} kind="teams"
                         slot={slot} field="name" read={row.name}
                         send={sendName} readOnly={locked}/>
            </Stack>
            {members.map((member, place) => (
                <MemberFields key={place} slot={slot} place={place}
                              member={member} characters={characters}
                              game={game} send={send} locked={locked}/>
            ))}
        </RowFrame>
    );
}

const MemoTeamRow = React.memo(TeamRowCard);

// The rows of the layout shown. A move says where each row goes, by its place
// and not by what is in it, so that it does not matter whether the page has
// read the last one yet (POST :reorder), and another can follow at once. The
// fields say which row they write to, and stop until the rows are read.
export function RowsCard({kind, players, teams, characters, game, sendPlayer,
                          sendTeam, reorder, notices, settling, importing}: {
    kind: RowKind;
    players: ResultPlayerRow[];
    teams: ResultTeamRow[];
    characters: Characters | null;
    game: string;
    sendPlayer: (slot: number, fields: PlayerFields) => Promise<unknown>;
    sendTeam: (slot: number, fields: TeamFields) => Promise<unknown>;
    reorder: (order: number[]) => void;
    // Why a select is disabled (a list not read), told above the rows with
    // the way to read it again.
    notices?: React.ReactNode;
    // A move awaits its answer and the read after it.
    settling: boolean;
    // The standings are on their way, to replace the rows.
    importing: boolean;
}) {
    const count = kind === "teams" ? teams.length : players.length;
    const [dropAt, setDropAt] = React.useState<number | null>(null);
    const dragFrom = React.useRef<number | null>(null);
    const box = React.useRef<HTMLDivElement>(null);
    const reorderRef = React.useRef(reorder);
    reorderRef.current = reorder;
    const countRef = React.useRef(count);
    countRef.current = count;

    const moveTo = React.useCallback((from: number, at: number) => {
        const slots = Array.from({length: countRef.current},
                                 (_unused, place) => String(place + 1));
        const order = movedOrder(slots, slots[from], at);
        if (order.join() !== slots.join()) {
            reorderRef.current(order.map(Number));
        }
    }, []);

    const move = React.useCallback((index: number, delta: -1 | 1) => {
        moveTo(index, delta < 0 ? index - 1 : index + 2);
        // The focus goes along with the row, so that the key can be pressed
        // again; at the end of the list, to the way back.
        const row = box.current?.querySelector(
            `[data-result-row="${index + delta + 1}"]`);
        const way = delta < 0 ? "up" : "down";
        const button = row?.querySelector<HTMLButtonElement>(
            `[data-move="${way}"]:not(:disabled)`)
            ?? row?.querySelector<HTMLButtonElement>(
                "[data-move]:not(:disabled)");
        button?.focus();
    }, [moveTo]);

    const drag: RowDrag = React.useMemo(() => ({
        onGripStart(index, event) {
            // What is being typed goes to the row it was typed into: leaving
            // the field sends it now, ahead of the reorder (serial writes).
            const active = document.activeElement as HTMLElement | null;
            active?.blur?.();
            dragFrom.current = index;
            const transfer = event.dataTransfer;
            if (transfer) {
                transfer.setData(DRAG_TYPE, String(index));
                transfer.effectAllowed = "move";
                const card = (event.currentTarget as HTMLElement)
                    .closest("[data-result-row]");
                if (card && transfer.setDragImage) {
                    transfer.setDragImage(card, 16, 16);
                }
            }
        },
        onEnd() {
            dragFrom.current = null;
            setDropAt(null);
        },
        onOver(index, event) {
            if (dragFrom.current === null) {
                return;  // not one of these rows
            }
            event.preventDefault();
            setDropAt(dropIndex(index, event));
        },
        onDrop(index, event) {
            const from = dragFrom.current;
            dragFrom.current = null;
            setDropAt(null);
            if (from === null) {
                return;
            }
            event.preventDefault();
            moveTo(from, dropIndex(index, event));
        },
    }), [moveTo]);

    const locked = settling || importing;
    const rows = kind === "teams"
        ? teams.map((row, index) => (
            <React.Fragment key={row.slot}>
                {dropAt === index && <DropLine/>}
                <MemoTeamRow row={row} index={index} count={count}
                             characters={characters} game={game} send={sendTeam}
                             move={move} drag={drag} locked={locked}
                             importing={importing}/>
            </React.Fragment>))
        : players.map((row, index) => (
            <React.Fragment key={row.slot}>
                {dropAt === index && <DropLine/>}
                <MemoPlayerRow row={row} index={index} count={count}
                               characters={characters} game={game}
                               send={sendPlayer} move={move} drag={drag}
                               locked={locked} importing={importing}/>
            </React.Fragment>));

    const title = i18n.t(`result_rows_${kind}`, {count});
    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1} ref={box}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    <Note>{i18n.t("result_rows_note")}</Note>
                    {notices}
                    {rows}
                    {dropAt === count && <DropLine/>}
                </Stack>
            </CardContent>
        </Card>
    );
}

// ── 選手を DB に保存 ─────────────────────────────────────────────────────

export function SaveToDbCard({save}: {save: () => void}) {
    return (
        <CardFrame title={i18n.t("result_save_db")}>
            <Note>{i18n.t("result_save_db_note")}</Note>
            <Box>
                <Button variant="outlined" onClick={save}>
                    {i18n.t("result_save_db")}
                </Button>
            </Box>
        </CardFrame>
    );
}

// ── プレビューと作成 ─────────────────────────────────────────────────────

function generateStatus(generating: boolean,
                        result: GenerateResult | null): string {
    if (generating) {
        return i18n.t("result_generating");
    }
    if (!result) {
        return "";
    }
    return result.status === "done"
        ? i18n.t("result_saved", {path: result.path})
        : errorText(result.error);
}

export function PreviewCard({canRender, render, layout, imageRevision, sample,
                             previewing, previewError, generating, blocked,
                             result, noData, postState, generate, copy, postToX,
                             readPost}: {
    // False on the headless shell: there is no off-screen view to draw with.
    canRender: boolean;
    render: ResultRender;
    layout: ResultLayout | undefined;
    // The newest run that ended with an image (0: none yet).
    imageRevision: number;
    // A preview now draws the sample: no row has anything to draw.
    sample: boolean;
    previewing: boolean;
    // A preview of this page's that failed: its code ("" none).
    previewError: string;
    generating: boolean;
    // The page's request awaits its answer.
    blocked: boolean;
    result: GenerateResult | null;
    // A generation or a post was asked for with nothing to draw.
    noData: boolean;
    postState: PostState;
    generate: () => void;
    // The image, made again first when what it is made from has changed.
    copy: () => void;
    postToX: () => void;
    // The post's text again, when it could not be read.
    readPost: () => void;
}) {
    const [failedRevision, setFailedRevision] = React.useState(0);
    const shown = imageRevision > 0 && failedRevision !== imageRevision;
    const made = Boolean(render.path);
    const url = made ? generatedUrl(render) : "";
    const status = generateStatus(generating, result);
    const canCopy = canCopyImage();

    return (
        <CardFrame title={i18n.t("result_preview")}>
            {!canRender && (
                <Alert severity="info">{i18n.t("result_cannot_render")}</Alert>
            )}
            {/* The layout's shape, within the pane's height: a 960 wide one
                is as tall as it is wide or more. */}
            <Box
                sx={{position: "relative", width: "100%",
                     aspectRatio: layout
                         ? `${layout.width} / ${layout.height}` : "16 / 9",
                     maxHeight: "60vh", display: "flex", alignItems: "center",
                     justifyContent: "center", border: 1,
                     borderColor: "divider", borderRadius: 1,
                     overflow: "hidden", bgcolor: "action.hover"}}
            >
                {shown ? (
                    <Box component="img" alt={i18n.t("result_preview_image")}
                         src={previewUrl(imageRevision)}
                         onError={() => setFailedRevision(imageRevision)}
                         sx={{width: "100%", height: "100%",
                              objectFit: "contain"}}/>
                ) : (
                    <Typography variant="body2" color="text.secondary">
                        {imageRevision > 0
                            ? i18n.t("result_preview_failed_to_show")
                            : i18n.t("result_preview_none")}
                    </Typography>
                )}
                {sample && canRender && (
                    <Chip size="small" color="warning"
                          label={i18n.t("result_sample")}
                          sx={{position: "absolute", top: 8, left: 8}}/>
                )}
                {previewing && (
                    <Stack direction="row" spacing={1} alignItems="center"
                           sx={{position: "absolute", top: 8, right: 8,
                                px: 1, py: 0.5, borderRadius: 1,
                                bgcolor: "background.paper"}}>
                        <CircularProgress size={16}/>
                        <Typography variant="caption">
                            {i18n.t("result_previewing")}
                        </Typography>
                    </Stack>
                )}
            </Box>
            {sample && canRender && <Note>{i18n.t("result_sample_note")}</Note>}
            <Note>{i18n.t("result_background_note")}</Note>
            {previewError && (
                <Typography variant="body2" color="error">
                    {errorText(previewError)}
                </Typography>
            )}
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap"
                   alignItems="center">
                <Button variant="contained"
                        disabled={!canRender || generating || blocked}
                        onClick={generate}>
                    {i18n.t("result_generate")}
                </Button>
                {/* The result made (out/results), not the preview. An image
                    reaches the clipboard from a secure context only (this
                    PC); a page from another computer over http offers it to
                    open or save instead. */}
                {canCopy ? (
                    <Button variant="outlined" disabled={!made || generating}
                            onClick={copy}>
                        {i18n.t("result_copy_image")}
                    </Button>
                ) : (
                    <Button variant="outlined" component="a"
                            href={made ? url : undefined}
                            target="_blank" rel="noopener noreferrer"
                            disabled={!made || generating}>
                        {i18n.t("result_open_image")}
                    </Button>
                )}
                <Button variant="contained" color="secondary"
                        disabled={!canRender || generating || blocked
                            || postState !== "ready"}
                        onClick={postToX}>
                    {i18n.t("result_post")}
                </Button>
            </Stack>
            <Note>
                {i18n.t(canCopy ? "result_post_note" : "result_post_note_no_copy")}
            </Note>
            {postState === "token" && (
                <Note>{i18n.t("result_post_token")}</Note>
            )}
            {postState === "failed" && (
                <Alert
                    severity="warning"
                    action={
                        <Button color="inherit" size="small" onClick={readPost}>
                            {i18n.t("result_reload")}
                        </Button>
                    }
                >
                    {i18n.t("result_post_failed")}
                </Alert>
            )}
            {noData && sample && (
                <Alert severity="info">{i18n.t("result_no_data")}</Alert>
            )}
            {status && (
                <Typography variant="body2" role="status"
                            color={result?.status === "error" && !generating
                                ? "error" : "text.secondary"}
                            sx={{wordBreak: "break-all"}}>
                    {status}
                </Typography>
            )}
        </CardFrame>
    );
}
