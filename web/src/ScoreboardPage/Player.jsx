import React from "react";
import {
    Autocomplete,
    Card,
    CardContent,
    Chip,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    Button,
    IconButton,
    Link,
    Stack,
    useMediaQuery
} from "@mui/material";
import TextField from './TextField';
import i18n from "../i18n/config";
import {Close} from "@mui/icons-material";
import {useTheme} from "@mui/material/styles";
import {CharSelector} from "./CharSelector";
import {useSelector} from "react-redux";
import {CountrySelector} from "../CountrySelector";
import {CountryStateSelector} from "../CountryStateSelector";
import {apiPost} from "../api";
import {useApiAction} from "./Feedback";
import {DisplayOptionsMenuButton, useDisplayOptions} from "./displayOptions";

/**
 * @typedef {{
 *    charIdx: string,
 *    charName: string,
 *    charSkin: string | number
 * }} CharacterSelection
 */

/**
 * @typedef {Record.<number, CharacterSelection>} CharacterSelections
 */

/** @typedef {{
 *     _countryCode?: string,
 *     _stateCode?: string,
 *     _team: string,
 *     _name: string,
 *     _realName: string,
 *     _twitter: string,
 *     _customTextbox: string,
 *     _characterSelections: CharacterSelections
 *
 *     countryCode?: string,
 *     stateCode?: string,
 *     team: string,
 *     name: string,
 *     realName: string,
 *     twitter: string,
 *     customTextbox: string,
 *     characterSelections: CharacterSelections
 *     expanded: boolean
 * }} PlayerState
 */

/**
 * @param {TSHPlayerDbEntry} player
 * @param {string} gameCodename
 * @param {number} count
 * @return {CharacterSelections}
 */
function getMains(player, gameCodename, count) {
    const /** @type CharacterSelections */ rval = {};
    // Yeah, we do 1-based counting here.
    for (let i = 1; i < count+1; i += 1) {
        rval[i] = {
            charName: "",
            charSkin: "",
            charIdx: i
        };
    }

    if (player.mains?.hasOwnProperty(gameCodename)) {
        const mains = player.mains?.[gameCodename];
        if (mains) {
            for (let i = 0; i < count && i < mains.length; i += 1) {
                const main = mains[i]
                rval[i+1] = {
                    charName: main?.[0] ?? "",
                    charSkin: main?.[1] ?? "",
                    charIdx: i+1
                }
            }
        }
    }

    return rval;
}

/**
 * Player form. Mirrors the desktop Score tab: every field submits on commit
 * (blur / selection), there is no separate submit button, and field
 * visibility follows the shared `display_options.show_*` state.
 *
 * @param {string} teamId
 * @param {string} teamKey
 * @param {TSHPlayerInfo} player
 * @param {number|string} scoreboardNumber
 * @param {string} tshTeamId
 */
export default function Player({teamId, teamKey, player, scoreboardNumber, tshTeamId}) {
    const [
        /** @type {PlayerState} */ state,
        setState
    ] = React.useState({
        _countryCode: player.country?.code,
        _stateCode: player.state?.code,
        _team: player.team,
        _name: player.name,
        _realName: player.real_name,
        _twitter: player.twitter,
        _customTextbox: player.custom_textbox ?? "",
        _charSelections: charsFromTsh(player.character),

        countryCode: player.country?.code,
        stateCode: player.state?.code,
        team: player.team,
        name: player.name,
        realName: player.real_name,
        twitter: player.twitter,
        customTextbox: player.custom_textbox ?? "",
        charSelections: charsFromTsh(player.character),
    });
    // Details modal. Its fields render from the same local state the parent
    // card already holds (loaded with the page's first view) — opening it
    // never fetches anything.
    const [detailsOpen, setDetailsOpen] = React.useState(false);

    const playerId = `${teamId}-p-${teamKey}`;
    const tshState = useSelector((s) => s.tshState.tshState);
    const gameCodename = tshState?.game?.codename;
    /** @type TSHPlayerDb */ const playerDb = useSelector(state => state.tshPlayers.players);
    const show = useDisplayOptions();
    const {run} = useApiAction();
    const theme = useTheme();
    const fullScreenDialog = useMediaQuery(theme.breakpoints.down('sm'));
    const cardRef = React.useRef(null);
    // The dialog renders in a portal (outside cardRef), so the echo guard
    // needs its own ref.
    const dialogRef = React.useRef(null);

    React.useEffect(() => {
        const newProps = {
            countryCode: player.country?.code,
            stateCode: player.state?.code,
            team: player.team,
            name: player.name,
            realName: player.real_name,
            twitter: player.twitter,
            customTextbox: player.custom_textbox ?? "",
            charSelections: charsFromTsh(player.character),
        }

        const stateUpdates = {};
        for (const prop in newProps) {
            stateUpdates[`_${prop}`] = newProps[prop];
        }
        // Server echoes arrive right after every commit (immediate-submit
        // model). Don't clobber the visible fields while the operator is
        // typing inside this card or its details dialog; the `_` baselines
        // always track the server.
        const editingHere =
            cardRef.current?.contains(document.activeElement)
            || dialogRef.current?.contains(document.activeElement);
        if (!editingHere) {
            for (const prop in newProps) {
                stateUpdates[prop] = newProps[prop];
            }
        }

        setState(s => ({...s, ...stateUpdates}));
    }, [player]) // eslint-disable-line react-hooks/exhaustive-deps
    // We don't want our dependencies to be exhaustive above because we want to specifically only

    const /** @type {TSHCharacterDb} */ characters = useSelector(state => state.tshCharacters.characters);

    /** @returns {TSHPlayerInfo} */
    const getModifiedPlayerData = (s = state) => {
        /*
         * The fields here are slightly off... The API sends the scoreboard out with the real name in the
         * player's "real_name" field and the tag in the "name" field. But on save it uses a slightly different
         * format where "name" is where the real name is stored and "gamerTag" holds the tag.
         *
         * In general the field names just... don't line up.
         */
        const rval = {};
        rval.country_code = s.countryCode;
        rval.state_code = s.stateCode;
        rval.prefix = s.team
        rval.gamerTag = s.name
        rval.name = s.realName
        rval.twitter = s.twitter
        rval.custom_textbox = s.customTextbox
        rval.mains = {[gameCodename]: []}

        /*
         * When we submit character selection, it also updates the player's mains in the player DB. To prevent
         * overriding them when we haven't selected a character, we do this song-and-dance to submit the selected
         * characters and fall back
         */
        const lookupName = !!player.prefix ? `${player.prefix} ${player.name}` : player.name;
        const existingMains = Object.values(playerDb[lookupName]?.mains?.[gameCodename] ?? [])
        const charsFromForm = Object.values(s.charSelections)
        // The number of characters from the original TSH state.
        const maxChars = Object.values(s._charSelections).length;
        const mainsToSubmit = [];
        for (let i = 0; i < maxChars; i += 1) {
            if (i < charsFromForm.length && !!charsFromForm[i].charName) {
                mainsToSubmit.push([
                    (charsFromForm[i].charName ? characters[charsFromForm[i].charName]?.en_name : ""),
                    charsFromForm[i].charSkin || 0,
                    ""
                ])
            } else if (i < existingMains.length) {
                mainsToSubmit.push(existingMains[i]);
            } else {
                mainsToSubmit.push(["", 0, ""])
            }
        }
        rval.mains[gameCodename] = mainsToSubmit;

        // Don't persist player info to DB if they have no tag.
        rval.savePlayerToDb = (!!s.name);

        return rval;
    }

    // Immediate-submit (desktop editingFinished equivalent). Pass the state
    // explicitly for selection-type commits where setState hasn't flushed.
    const submitPlayer = (fromState = state) => {
        run(() => apiPost(
            `/scoreboard${scoreboardNumber}-update-team-${tshTeamId}-${teamKey}`,
            getModifiedPlayerData(fromState)));
    };

    const changeHandlerFor = (fieldName) => {
        return (/** React.ChangeEvent<HTMLInputElement> | string */ e) => {
            const newVal = (
                typeof e === 'string' ? e
                    : e === null || e === undefined ? ''
                    : e?.target?.value
            );

            const newState = {
                ...state,
                [fieldName]: newVal
            };

            setState(newState);
            return newState;
        }
    }

    // Selects commit in a single gesture, so they submit right away.
    const selectHandlerFor = (fieldName) => {
        const change = changeHandlerFor(fieldName);
        return (e) => {
            submitPlayer(change(e));
        };
    }

    /**
     *
     * @param {React.SyntheticEvent} event
     * @param {TSHPlayerDbEntry|string} newPlayer
     * @param {string} reason
     */
    const onTagChanged = (event, newPlayer,  reason) => {
        if (newPlayer instanceof Object && newPlayer.hasOwnProperty("gamerTag")) {
            // newPlayer is a TSHPlayerDbEntry
            const newState = {
                ...state,
                countryCode: newPlayer.country_code ?? "",
                name: newPlayer.gamerTag,
                team: newPlayer.prefix ?? "",
                realName: newPlayer.name ?? "",
                twitter: newPlayer.twitter ?? "",
                stateCode: newPlayer.state_code ?? "",
                charSelections: getMains(newPlayer, gameCodename, Object.keys(state._charSelections).length),
            };
            setState(newState);
            submitPlayer(newState);
        } else {
            // value is an ad-hoc string of a player tag; submitted on blur.
            setState({...state, name: newPlayer})
        }
    };

    /**
     *
     * @param charIdx
     * @param {TSHCharacterBase|string} newChar
     */
    const onCharNameChanged = (charIdx, newChar) => {
        const charEntry = characters?.[newChar?.en_name];
        let charSkin = "";
        if (charEntry) {
            const charSkins = charEntry?.skins?.length;
            if (charSkins && charSkins > 0) {
                charSkin = 0;
            }
        }

        const newState = {
            ...state,
            charSelections: {
                ...state.charSelections,
                [charIdx]: {
                    charIdx,
                    charName: newChar?.en_name ?? newChar,
                    charSkin: charSkin
                }
            }
        };
        setState(newState);
        submitPlayer(newState);
    }

    const onCharSkinChanged = (charIdx, newSkinIdx) => {
        const newState = {
            ...state,
            charSelections: {
                ...state.charSelections,
                [charIdx]: {
                    ...(state.charSelections?.[charIdx] ?? {}),
                    charSkin: newSkinIdx
                }
            }
        };
        setState(newState);
        submitPlayer(newState);
    }

    const idBase = `team-${teamId}-player-${playerId}-`;
    // Phones stack the form fields vertically at full width; sm+ keeps the
    // two-column rows.
    const rowProps = {direction: {xs: 'column', sm: 'row'}, spacing: {xs: 1.5, sm: 2}};

    return (
        <Card raised={true} ref={cardRef}>
            <CardContent sx={{padding: 1.5, '&:last-child': {paddingBottom: 1.5}}}>
                <Stack spacing={1.5}>
                    {/* Inline: only the tag and the sponsor. Everything else
                        lives in the details dialog. */}
                    <Stack {...rowProps} alignItems="center">
                        {show.show_sponsor && (
                            <TextField label={i18n.t("sponsor")}
                                       key={idBase + "sponsor"}
                                       id={idBase + "sponsor"}
                                       value={state.team ?? ''}
                                       onChange={changeHandlerFor('team')}
                                       onBlur={() => submitPlayer()}
                                       sx={{width: {xs: '100%', sm: '35%'}, minWidth: 0}}
                            />
                        )}

                        {show.show_name && (
                            <Autocomplete
                                key={idBase + "tag"}
                                id={idBase + "tag"}
                                size="small"
                                options={Object.values(playerDb)}
                                value={state.name ?? ''}
                                getOptionLabel={(/** TSHPlayerDbEntry|string */ player) => {
                                    return player?.prefixed_tag ?? player;
                                }}
                                freeSolo={true}
                                sx={{
                                    // Sums to 100% with the sponsor field above (was 35% + 100% = 135%,
                                    // which flexbox silently auto-corrected via shrink but made the
                                    // intended split ambiguous). Also drop MUI's built-in 30px input
                                    // floor (.MuiAutocomplete-input) so this field can shrink as far as
                                    // its sibling on narrow layouts instead of protecting a fixed floor.
                                    width: {xs: '100%', sm: show.show_sponsor ? '65%' : '100%'},
                                    minWidth: 0,
                                    '& .MuiAutocomplete-input': {minWidth: 0},
                                }}
                                onChange={onTagChanged}
                                onBlur={() => submitPlayer()}
                                renderInput={(params) =>
                                    <TextField
                                        {...params}
                                        label={i18n.t("tag")}
                                    />}
                            />
                        )}
                    </Stack>

                    <Link
                        component="button"
                        type="button"
                        underline="hover"
                        variant="body2"
                        onClick={() => setDetailsOpen(true)}
                        sx={{alignSelf: 'flex-start'}}
                    >
                        {`>>${i18n.t("details")}`}
                    </Link>
                </Stack>
            </CardContent>

            {/* Details dialog: renders the state the page already loaded
                with its first view — opening it fetches nothing. Fields
                submit on commit exactly like the inline ones. */}
            <Dialog
                open={detailsOpen}
                onClose={() => setDetailsOpen(false)}
                fullWidth
                maxWidth="sm"
                fullScreen={fullScreenDialog}
            >
                <DialogTitle sx={{display: 'flex', alignItems: 'center', gap: 1, paddingRight: 6}}>
                    {state.name || i18n.t("tag")}
                    {/* Desktop parity: the seed is a read-only badge
                        (set by loading a set), not an input. */}
                    {show.show_seed && Number(player.seed) > 0 && (
                        <Chip
                            size="small"
                            variant="outlined"
                            label={`${i18n.t("seed")} ${player.seed}`}
                        />
                    )}
                    {/* Field-visibility eye menu (global toggles; moved here
                        from the header). */}
                    <DisplayOptionsMenuButton
                        sx={{position: 'absolute', right: 44, top: 8}}
                    />
                    <IconButton
                        size="small"
                        onClick={() => setDetailsOpen(false)}
                        sx={{position: 'absolute', right: 8, top: 8}}
                    >
                        <Close />
                    </IconButton>
                </DialogTitle>
                <DialogContent ref={dialogRef}>
                    <Stack spacing={2} paddingTop={1}>
                        {(show.show_real_name || show.show_social) && (
                            <Stack {...rowProps}>
                                {show.show_real_name && (
                                    <TextField label={i18n.t("real_name")}
                                               key={idBase + "real-name"}
                                               id={idBase + "real-name"}
                                               value={state.realName ?? ''}
                                               onChange={changeHandlerFor('realName')}
                                               onBlur={() => submitPlayer()}
                                               sx={{width: {xs: '100%', sm: '50%'}}}
                                    />
                                )}
                                {show.show_social && (
                                    <TextField label={i18n.t("twitter")}
                                               key={idBase + "twitter"}
                                               id={idBase + "twitter"}
                                               value={state.twitter ?? ''}
                                               onChange={changeHandlerFor('twitter')}
                                               onBlur={() => submitPlayer()}
                                               sx={{width: {xs: '100%', sm: show.show_real_name ? '50%' : '100%'}}}
                                    />
                                )}
                            </Stack>
                        )}

                        {show.show_location && (
                            <Stack {...rowProps}>
                                <CountrySelector
                                    sx={{width: {xs: '100%', sm: '50%'}, maxWidth: {xs: '100%', sm: '50%'}}}
                                    label={i18n.t("country")}
                                    value={state.countryCode ?? ''}
                                    onChange={selectHandlerFor('countryCode')}
                                />

                                <CountryStateSelector
                                    countryCode={state.countryCode}
                                    sx={{width: {xs: '100%', sm: '50%'}, maxWidth: {xs: '100%', sm: '50%'}}}
                                    label={i18n.t("state")}
                                    value={state.stateCode ?? ''}
                                    onChange={selectHandlerFor('stateCode')}
                                />
                            </Stack>
                        )}

                        {show.show_characters && (
                            Object.values(state.charSelections).map( (cs) =>
                                <CharSelector
                                    key={`${playerId}-cs-${cs.charIdx}`}
                                    id={`${playerId}-cs-${cs.charIdx}`}
                                    charName={cs.charName}
                                    charSkin={cs.charSkin}
                                    onCharNameChanged={(ev, val, _) => onCharNameChanged(cs.charIdx, val)}
                                    onCharSkinChanged={(ev, val, _) => onCharSkinChanged(cs.charIdx, ev.target.value)}
                                    // Character + skin are companion fields in
                                    // a fixed-height row: keep them side by
                                    // side even on xs (the column stacking of
                                    // rowProps breaks the 40px row).
                                    stackProps={{direction: 'row', spacing: {xs: 1.5, sm: 2}}}
                                />
                            )
                        )}

                        {show.show_additional && (
                            <TextField label={i18n.t("show_additional")}
                                       key={idBase + "additional"}
                                       id={idBase + "additional"}
                                       multiline
                                       minRows={2}
                                       value={state.customTextbox ?? ''}
                                       onChange={changeHandlerFor('customTextbox')}
                                       onBlur={() => submitPlayer()}
                            />
                        )}
                    </Stack>
                </DialogContent>
                <DialogActions>
                    <Button variant="outlined" size="small" onClick={() => setDetailsOpen(false)}>
                        {i18n.t("close")}
                    </Button>
                </DialogActions>
            </Dialog>
        </Card>
    )
}

/**
 *
 * @param {TSHCharacterSelections} tshChars
 * @returns {CharacterSelections}
 */
function charsFromTsh(tshChars) {
    const res = {};
    for (const i in tshChars) {
        res[i] = {
            charIdx: i,
            charName: tshChars[i].en_name ?? "",
            // Never undefined: the skin <Select> must stay controlled from
            // its first render (MUI warns on undefined -> value switches).
            charSkin: tshChars[i].skin ?? "",
        }

    }
    return res;
}
