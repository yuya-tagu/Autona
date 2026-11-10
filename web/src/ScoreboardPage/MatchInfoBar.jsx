import React from "react";
import {Autocomplete, Stack} from "@mui/material";
import TextField from "./TextField";
import {NumberInput} from "../NumberInput";
import {apiGetJson, apiPatch} from "../api";
import {useApiAction} from "./Feedback";
import i18n from "../i18n/config";

/**
 * The three lists of GET /api/v1/match-names/candidates as Autocomplete
 * takes its `options`: arrays of strings, whatever the answer holds. Not
 * GET /api/v1/match-names: that is the terms by key (objects), and an
 * Autocomplete handed an object throws when its popup opens, taking the
 * whole page down.
 */
export function candidateLists(data) {
    const list = (value) => (Array.isArray(value)
        ? value.filter((name) => typeof name === "string")
        : []);
    return {phase: list(data?.phase), pool: list(data?.pool), match: list(data?.match)};
}

/**
 * Match metadata bar, mirroring the desktop scoreBar row above the team
 * columns: editable Phase / Pool / Match (with the candidates the desktop's
 * combos offer for this board, from GET /api/v1/match-names/candidates
 * ?board=N) and Best Of. Every field submits on commit — there is no
 * separate submit button (desktop editingFinished parity).
 */
export default function MatchInfoBar({scoreboardNumber, phase, pool, match, bestOf, action}) {
    const [localPhase, setLocalPhase] = React.useState(phase ?? "");
    const [localPool, setLocalPool] = React.useState(pool ?? "");
    const [localMatch, setLocalMatch] = React.useState(match ?? "");
    const [candidates, setCandidates] = React.useState(() => candidateLists(null));
    // The latest ask for the candidates: a reply to an earlier one is dropped.
    const latestAsk = React.useRef(0);
    const {run} = useApiAction();

    React.useEffect(() => { setLocalPhase(phase ?? ""); }, [phase]);
    React.useEffect(() => { setLocalPool(pool ?? ""); }, [pool]);
    React.useEffect(() => { setLocalMatch(match ?? ""); }, [match]);

    // Boards 1/2 offer the loaded tournament's pools and entrant rounds
    // besides the terms (core's lists, ADR-005 S4x), so the lists are not
    // kept for the page: they are asked for again whenever a combo opens,
    // and a tournament loaded meanwhile is in them.
    const loadCandidates = React.useCallback(() => {
        const ask = ++latestAsk.current;
        apiGetJson(`/api/v1/match-names/candidates?board=${scoreboardNumber}`)
            .then(({data}) => {
                if (ask === latestAsk.current) {
                    setCandidates(candidateLists(data));
                }
            })
            .catch(console.error); // candidates are a nicety; fail quietly
    }, [scoreboardNumber]);

    React.useEffect(() => {
        loadCandidates();
        // Another board, or gone: what is still on its way is not this one's.
        return () => { latestAsk.current += 1; };
    }, [loadCandidates]);

    // PATCH /api/v1/scoreboards/{n}/set: a partial update, only the keys sent
    // are applied.
    const submitField = (fields) => {
        run(() => apiPatch(`/api/v1/scoreboards/${scoreboardNumber}/set`, fields));
    };

    const submitPhase = (value) => {
        if ((value ?? "") !== (phase ?? "")) {
            submitField({phase: value ?? ""});
        }
    };
    const submitPool = (value) => {
        if ((value ?? "") !== (pool ?? "")) {
            submitField({pool: value ?? ""});
        }
    };
    const submitMatch = (value) => {
        if ((value ?? "") !== (match ?? "")) {
            submitField({match: value ?? ""});
        }
    };
    const onBestOfChanged = (_, newVal) => {
        // A cleared field reports null/undefined; bestOf must be an integer
        // (the API answers 400 otherwise), so there is nothing to send.
        if (newVal === null || newVal === undefined || Number.isNaN(newVal)) return;
        submitField({bestOf: newVal});
    };

    // On phones the phase and match combos share one row (50% each, minus
    // the flex gap); sm+ lets them flex on a single line with Best Of.
    const comboSx = {flex: 1, minWidth: {xs: 'calc(50% - 8px)', sm: 140}};

    return (
        <Stack
            direction="row"
            gap={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
        >
            <Autocomplete
                freeSolo
                size="small"
                options={candidates.phase}
                inputValue={localPhase}
                onInputChange={(e, v) => setLocalPhase(v ?? "")}
                onOpen={loadCandidates}
                onChange={(e, v) => submitPhase(v)}
                onBlur={() => submitPhase(localPhase)}
                sx={comboSx}
                renderInput={(params) => <TextField {...params} label={i18n.t("phase")} />}
            />
            <Autocomplete
                freeSolo
                size="small"
                options={candidates.pool}
                inputValue={localPool}
                onInputChange={(e, v) => setLocalPool(v ?? "")}
                onOpen={loadCandidates}
                onChange={(e, v) => submitPool(v)}
                onBlur={() => submitPool(localPool)}
                sx={comboSx}
                renderInput={(params) => <TextField {...params} label={i18n.t("pool")} />}
            />
            <Autocomplete
                freeSolo
                size="small"
                options={candidates.match}
                inputValue={localMatch}
                onInputChange={(e, v) => setLocalMatch(v ?? "")}
                onOpen={loadCandidates}
                onChange={(e, v) => submitMatch(v)}
                onBlur={() => submitMatch(localMatch)}
                sx={comboSx}
                renderInput={(params) => <TextField {...params} label={i18n.t("match")} />}
            />
            <NumberInput
                label={i18n.t("best_of", {value: ""})}
                wingWidth={40}
                width={170}
                min={0}
                value={bestOf ?? 0}
                onChange={onBestOfChanged}
            />
            {action}
        </Stack>
    );
}
