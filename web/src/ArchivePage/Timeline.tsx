import React from "react";
import {Box, ButtonBase, ToggleButton, ToggleButtonGroup, Typography} from "@mui/material";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import i18n from "../i18n/config";
import {DARK_TOKENS as tokens} from "../themes";
import {SetRow, SetsJson, formatClock, rowText} from "./archiveSets";
import {
    Range,
    TimelineView,
    gameMarks,
    playerLanes,
    playheadPercent,
    setAt,
    shadeLevel,
    spanPercent,
    tickLabel,
    ticks,
    toPercent,
    toTime,
} from "./archiveTimeline";

// The archive page's timeline (ADR-006 A3, the mock's bottom band): the
// sets, the games (scoreboard corrections marked), the names read off the
// scoreboard (collapsed at first) and the playhead, over the whole video or
// the selected set. A click seeks (and picks the set under it); nothing is
// dragged (boundaries are edited in a later stage).

const LABEL_WIDTH = 110;
const RULER = 20;
const SETS = 30;
const GAMES = 12;
const PLAYER = 14;

// The mock's colours on the dark theme.
export const TIMELINE_COLORS = {
    matched: tokens.positive,
    review: tokens.gold,
    game: tokens.borderStrong,
    corrected: tokens.negative,
    playhead: tokens.brand,
    selected: tokens.textPrimary,
    shades: ["", "#184f95", "#1c5cab", "#2a78d6", "#6da7ec"],
};

function Lane({height, range, time, onPoint, children, label, testId}: {
    height: number;
    range: Range;
    time: number;
    onPoint: (t: number, target: Element) => void;
    children?: React.ReactNode;
    label?: React.ReactNode;
    testId?: string;
}) {
    const click = (event: React.MouseEvent<SVGSVGElement>) => {
        const box = event.currentTarget.getBoundingClientRect();
        const fraction = box.width > 0
            ? (event.clientX - box.left) / box.width : 0;
        onPoint(toTime(fraction, range), event.target as Element);
    };
    const at = playheadPercent(time, range);
    const head = at === null ? null : `${at}%`;
    return (
        <>
            <Box sx={{pl: 1.5, fontSize: "0.72rem", color: tokens.textSecondary,
                      whiteSpace: "nowrap", overflow: "hidden",
                      textOverflow: "ellipsis", lineHeight: `${height}px`}}>
                {label}
            </Box>
            <svg width="100%" height={height} onClick={click}
                 data-testid={testId}
                 style={{display: "block", cursor: "pointer",
                         overflow: "hidden"}}>
                {children}
                {head === null ? null : (
                    <line x1={head} x2={head} y1={0} y2={height}
                          stroke={TIMELINE_COLORS.playhead} strokeWidth={2}
                          pointerEvents="none" data-testid="archive-playhead"/>
                )}
            </svg>
        </>
    );
}

export default function Timeline({sets, rows, selected, view, onView, time,
                                  onSeek, onPick, range}: {
    sets: SetsJson;
    rows: SetRow[];
    selected: SetRow | null;
    view: TimelineView;
    onView: (view: TimelineView) => void;
    time: number;
    onSeek: (t: number) => void;
    onPick: (no: number, t: number) => void;
    range: Range;
}) {
    const [playersOpen, setPlayersOpen] = React.useState(false);
    const listed = new Set(rows.map((row) => row.no));
    const marks = gameMarks(sets);
    const lanes = playerLanes(sets);
    const tickTimes = ticks(range);
    const wholeMinutes = tickTimes.length < 2
        || (tickTimes[1] - tickTimes[0]) % 60 === 0;

    // A click on a set picks it (at the time clicked); elsewhere it picks the
    // set under the time, else only seeks.
    const point = (t: number, target: Element) => {
        const block = target.closest("[data-timeline-set]");
        const no = block ? Number(block.getAttribute("data-timeline-set")) : NaN;
        const row = Number.isInteger(no)
            ? sets.sets.find((r) => r.no === no) ?? null
            : setAt(sets.sets, t);
        if (row) {
            onPick(row.no, t);
        } else {
            onSeek(t);
        }
    };
    const noMatch = i18n.t("archive_no_match");
    const legend: [string, string][] = [
        [TIMELINE_COLORS.matched, "archive_legend_matched"],
        [TIMELINE_COLORS.review, "archive_legend_review"],
        [TIMELINE_COLORS.game, "archive_legend_game"],
        [TIMELINE_COLORS.corrected, "archive_legend_corrected"],
    ];
    return (
        <Box component="section" aria-label={i18n.t("archive_timeline")}
             sx={{borderTop: "1px solid", borderColor: "divider",
                  bgcolor: tokens.bgPrimary}}>
            <Box sx={{display: "flex", alignItems: "center", gap: 1,
                      height: 36, px: 1.5, borderBottom: "1px solid",
                      borderColor: "divider"}}>
                <Typography variant="subtitle2">
                    {i18n.t("archive_timeline")}
                </Typography>
                <ToggleButtonGroup exclusive size="small" value={view}
                                   onChange={(_e, next) => next && onView(next)}>
                    <ToggleButton value="all" sx={{py: 0.25}}>
                        {i18n.t("archive_timeline_all")}
                    </ToggleButton>
                    <ToggleButton value="set" disabled={selected === null}
                                  sx={{py: 0.25}}>
                        {i18n.t("archive_timeline_set")}
                    </ToggleButton>
                </ToggleButtonGroup>
                <Box sx={{flex: 1}}/>
                {legend.map(([color, key]) => (
                    <Box key={key} component="span"
                         sx={{display: "inline-flex", alignItems: "center",
                              gap: 0.5, fontSize: "0.72rem",
                              color: tokens.textSecondary}}>
                        <Box component="i" sx={{width: 9, height: 9,
                                                borderRadius: "2px",
                                                bgcolor: color}}/>
                        {i18n.t(key)}
                    </Box>
                ))}
            </Box>
            <Box sx={{display: "grid", alignItems: "center", py: 0.5, pr: 1.75,
                      gridTemplateColumns: `${LABEL_WIDTH}px minmax(0, 1fr)`}}>
                <Lane height={RULER} range={range} time={time} onPoint={point}>
                    {tickTimes.map((t) => {
                        const x = toPercent(t, range);
                        return (
                            <g key={t}>
                                <line x1={`${x}%`} x2={`${x}%`} y1={14} y2={18}
                                      stroke={tokens.textMuted}/>
                                <text x={`${x}%`} y={11} fontSize={10}
                                      fill={tokens.textMuted}
                                      textAnchor={x > 97 ? "end" : x < 3 ? "start" : "middle"}>
                                    {tickLabel(t, wholeMinutes)}
                                </text>
                            </g>
                        );
                    })}
                </Lane>
                <Lane height={SETS} range={range} time={time} onPoint={point}
                      label={i18n.t("archive_lane_sets")}
                      testId="archive-timeline-sets">
                    {sets.sets.map((row) => {
                        const at = spanPercent(row.start, row.end, range);
                        if (at === null) {
                            return null;
                        }
                        const isSelected = row.no === selected?.no;
                        const text = rowText(row, noMatch);
                        return (
                            <svg key={row.no} x={`${at.left}%`} y={3}
                                 width={`${Math.max(at.width, 0.2)}%`}
                                 height={SETS - 6} data-timeline-set={row.no}
                                 opacity={listed.has(row.no) ? 1 : 0.3}>
                                <title>
                                    {`#${row.no} ${text} ${formatClock(row.start)} – ${formatClock(row.end)}`}
                                </title>
                                <rect width="100%" height="100%" rx={4}
                                      fill={row.needs_review
                                          ? TIMELINE_COLORS.review
                                          : TIMELINE_COLORS.matched}
                                      stroke={isSelected
                                          ? TIMELINE_COLORS.selected : "none"}
                                      strokeWidth={isSelected ? 2 : 0}/>
                                <text x={6} y={(SETS - 6) / 2 + 4} fontSize={11}
                                      fill="#0b0b0b" pointerEvents="none">
                                    {row.needs_review
                                        ? i18n.t("archive_review_badge")
                                        : text}
                                </text>
                            </svg>
                        );
                    })}
                </Lane>
                <Lane height={GAMES} range={range} time={time} onPoint={point}
                      label={i18n.t("archive_lane_games")}
                      testId="archive-timeline-games">
                    {marks.map((mark, index) => {
                        const at = spanPercent(mark.start, mark.end, range);
                        return at === null ? null : (
                            <rect key={index} x={`${at.left}%`} y={2}
                                  width={`${Math.max(at.width, 0.15)}%`}
                                  height={GAMES - 4} rx={2}
                                  data-corrected={mark.corrected || undefined}
                                  fill={mark.corrected
                                      ? TIMELINE_COLORS.corrected
                                      : TIMELINE_COLORS.game}>
                                <title>
                                    {`${formatClock(mark.start)} – ${formatClock(mark.end)}`}
                                </title>
                            </rect>
                        );
                    })}
                </Lane>
                <ButtonBase onClick={() => setPlayersOpen((open) => !open)}
                            aria-expanded={playersOpen}
                            sx={{justifyContent: "flex-start", pl: 1, gap: 0.25,
                                 height: 22, fontSize: "0.72rem",
                                 color: tokens.textSecondary}}>
                    <ChevronRightIcon sx={{fontSize: 14,
                                           transform: playersOpen
                                               ? "rotate(90deg)" : "none"}}/>
                    {i18n.t("archive_players_ocr")}
                </ButtonBase>
                <Typography variant="caption" sx={{color: tokens.textMuted}}>
                    {lanes.length === 0 ? i18n.t("archive_players_none")
                        : playersOpen ? i18n.t("archive_players_open")
                        : i18n.t("archive_players_closed",
                                 {count: lanes.length})}
                </Typography>
                {playersOpen ? lanes.map((lane) => (
                    <Lane key={lane.name} height={PLAYER} range={range}
                          time={time} onPoint={point} label={lane.name}
                          testId="archive-timeline-player">
                        {lane.cells.map((cell, index) => {
                            const at = spanPercent(cell.start, cell.end, range);
                            return at === null ? null : (
                                <rect key={index} x={`${at.left}%`} y={2}
                                      width={`${Math.max(at.width, 0.15)}%`}
                                      height={PLAYER - 4}
                                      fill={TIMELINE_COLORS.shades[
                                          shadeLevel(cell.share)]}>
                                    <title>
                                        {`${lane.name} ${Math.round(cell.share * 100)}%`}
                                    </title>
                                </rect>
                            );
                        })}
                    </Lane>
                )) : null}
            </Box>
        </Box>
    );
}
