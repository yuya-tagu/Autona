import React from "react";
import {Autocomplete, Stack} from "@mui/material";
import TextField from "./TextField";
import {NumberInput} from "../NumberInput";
import {apiGetJson, apiPatch} from "../api";
import {useApiAction} from "./Feedback";
import i18n from "../i18n/config";

// Fetched once per page load; the candidates are static locale data.
let matchNamesCache = null;

/**
 * Match metadata bar, mirroring the desktop scoreBar row above the team
 * columns: editable Phase / Match (with locale candidates from
 * GET /api/v1/match-names) and Best Of. Every field submits on commit — there
 * is no separate submit button (desktop editingFinished parity).
 */
export default function MatchInfoBar({scoreboardNumber, phase, pool, match, bestOf, action}) {
    const [localPhase, setLocalPhase] = React.useState(phase ?? "");
    const [localPool, setLocalPool] = React.useState(pool ?? "");
    const [localMatch, setLocalMatch] = React.useState(match ?? "");
    const [candidates, setCandidates] = React.useState({phase: [], match: []});
    const {run} = useApiAction();

    React.useEffect(() => { setLocalPhase(phase ?? ""); }, [phase]);
    React.useEffect(() => { setLocalPool(pool ?? ""); }, [pool]);
    React.useEffect(() => { setLocalMatch(match ?? ""); }, [match]);

    React.useEffect(() => {
        if (matchNamesCache) {
            setCandidates(matchNamesCache);
            return;
        }
        apiGetJson('/api/v1/match-names')
            .then(({data}) => {
                matchNamesCache = {
                    phase: data?.phase ?? [],
                    match: data?.match ?? [],
                };
                setCandidates(matchNamesCache);
            })
            .catch(console.error); // candidates are a nicety; fail quietly
    }, []);

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
                onChange={(e, v) => submitPhase(v)}
                onBlur={() => submitPhase(localPhase)}
                sx={comboSx}
                renderInput={(params) => <TextField {...params} label={i18n.t("phase")} />}
            />
            <Autocomplete
                freeSolo
                size="small"
                options={[]}
                inputValue={localPool}
                onInputChange={(e, v) => setLocalPool(v ?? "")}
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
