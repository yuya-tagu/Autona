import React from "react";
import {Autocomplete, Stack} from "@mui/material";
import TextField from "./TextField";
import {NumberInput} from "../NumberInput";
import {apiGet, apiGetJson} from "../api";
import {useApiAction} from "./Feedback";
import i18n from "../i18n/config";

// Fetched once per page load; the candidates are static locale data.
let matchNamesCache = null;

/**
 * Match metadata bar, mirroring the desktop scoreBar row above the team
 * columns: editable Phase / Match (with locale candidates from
 * GET /match-names) and Best Of. Every field submits on commit — there is
 * no separate submit button (desktop editingFinished parity).
 */
export default function MatchInfoBar({scoreboardNumber, phase, match, bestOf, action}) {
    const [localPhase, setLocalPhase] = React.useState(phase ?? "");
    const [localMatch, setLocalMatch] = React.useState(match ?? "");
    const [candidates, setCandidates] = React.useState({phase: [], match: []});
    const {run} = useApiAction();

    React.useEffect(() => { setLocalPhase(phase ?? ""); }, [phase]);
    React.useEffect(() => { setLocalMatch(match ?? ""); }, [match]);

    React.useEffect(() => {
        if (matchNamesCache) {
            setCandidates(matchNamesCache);
            return;
        }
        apiGetJson('/match-names')
            .then((data) => {
                matchNamesCache = {
                    phase: data?.phase ?? [],
                    match: data?.match ?? [],
                };
                setCandidates(matchNamesCache);
            })
            .catch(console.error); // candidates are a nicety; fail quietly
    }, []);

    const submitField = (params) => {
        run(() => apiGet(`/scoreboard${scoreboardNumber}-set?`
            + new URLSearchParams(params).toString()));
    };

    const submitPhase = (value) => {
        if ((value ?? "") !== (phase ?? "")) {
            submitField({phase: value ?? ""});
        }
    };
    const submitMatch = (value) => {
        if ((value ?? "") !== (match ?? "")) {
            submitField({match: value ?? ""});
        }
    };
    const onBestOfChanged = (_, newVal) => {
        if (Number.isNaN(newVal)) return;
        submitField({"best-of": newVal});
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
