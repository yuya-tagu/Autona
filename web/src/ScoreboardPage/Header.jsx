import {
    AppBar,
    Button,
    Collapse,
    FormControlLabel,
    IconButton,
    Switch,
    TextField,
    Toolbar,
    Typography,
    useMediaQuery
} from "@mui/material";
import {Box, Stack} from "@mui/system";
import {useTheme} from "@mui/material/styles";
import {shallowEqual, useSelector} from "react-redux";
import {BASE_URL} from "../env";
import {apiPost} from "../api";
import {useApiAction} from "./Feedback";
import i18n from "../i18n/config";
import React from "react";
import {Tune} from "@mui/icons-material";


export const Header = ({scoreboardNumber = 1, ...rest}) => {
    const autonaState = useSelector(state => state.autonaState.autonaState, shallowEqual);

    const theme = useTheme();

    const [tournamentUrl, setTournamentUrl] = React.useState("");
    const [autoUpdate, setAutoUpdate] = React.useState(false);
    const [autoUpdateInterval, setAutoUpdateInterval] = React.useState(10);
    const autoUpdateTimerRef = React.useRef(null);
    // On phones the tournament-URL / auto-update row starts collapsed so the
    // header takes a single line; sm+ always shows it.
    const isXs = useMediaQuery(theme.breakpoints.down('sm'));
    const [toolsOpen, setToolsOpen] = React.useState(false);

    const {pending: loadPending, run: runLoad} = useApiAction();

    const loadTournament = () => {
        if (!tournamentUrl) return;
        // 202 Accepted: the start.gg fetch continues on the desktop and its
        // result arrives over the program-state broadcast.
        runLoad(() => apiPost('/api/v1/tournament', {url: tournamentUrl}));
    };

    const pullStreamSet = () => {
        // Fired by the auto-update timer; keep failures out of the snackbar
        // so a briefly unreachable desktop app doesn't spam every interval.
        apiPost(`/api/v1/scoreboards/${scoreboardNumber}:pull-stream`).catch(console.error);
    };

    // Auto-update: periodically pull stream set
    React.useEffect(() => {
        if (autoUpdateTimerRef.current) {
            clearInterval(autoUpdateTimerRef.current);
            autoUpdateTimerRef.current = null;
        }
        if (autoUpdate) {
            autoUpdateTimerRef.current = setInterval(() => {
                pullStreamSet();
            }, autoUpdateInterval * 1000);
        }
        return () => {
            if (autoUpdateTimerRef.current) {
                clearInterval(autoUpdateTimerRef.current);
            }
        };
    }, [autoUpdate, autoUpdateInterval]);

    return (
        <AppBar
          sx={{
              position: 'unset',
              px: '2em',
          }}
          {...rest}
        >
            <Toolbar disableGutters sx={{
                whiteSpace: "nowrap",
                textOverflow: "ellipsis",
                display: 'flex',
                flexWrap: 'wrap',
                gap: 1,

                [theme.breakpoints.down('sm')]: {
                    gap: 0.5,
                    minHeight: 48,
                },
            }}>

                <Typography
                    variant="h6"
                    gap={2}
                    sx={{
                        display: 'flex',
                        alignItems: "center",
                        mr: {xs: 1, sm: 2},
                    }}
                >
                    <Box
                        component="img"
                        alt="Autona logo"
                        src={`${BASE_URL}/assets/icons/icon.png`}
                        sx={{
                            height: {xs: 32, sm: 48},
                            width: {xs: 32, sm: 48},
                        }}
                    />
                    <Box component="span" sx={{display: {xs: 'none', md: 'inline'}, ml: 1}}>
                        Web Scoreboard
                    </Box>
                </Typography>

                {/* Event name */}
                <Box sx={{
                    overflowX: "hidden",
                    textOverflow: "ellipsis",
                    maxWidth: '100%',
                    minWidth: 0,
                    flexShrink: 1,
                    mr: {xs: 0, sm: 2},
                    display: {xs: 'none', sm: 'block'},
                }}>
                    <span>Event:&nbsp;</span>
                    <span>{autonaState.tournamentInfo.tournamentName}:</span>
                    <span>{autonaState.tournamentInfo.eventName}</span>
                </Box>

                {/* Tools toggle (phone only): tournament URL + auto-update.
                    The field-visibility eye menu lives in each player's
                    details dialog (DisplayOptionsMenuButton). */}
                <IconButton
                    color="inherit"
                    size="small"
                    onClick={() => setToolsOpen(!toolsOpen)}
                    title={i18n.t("tournament_url")}
                    sx={{ml: 'auto', display: {xs: 'inline-flex', sm: 'none'}}}
                >
                    <Tune />
                </IconButton>
            </Toolbar>

            {/* Second toolbar row: Tournament URL + Auto-update.
                Collapsed by default on phones (Tune button toggles). */}
            <Collapse in={toolsOpen || !isXs}>
            <Toolbar
                disableGutters
                variant="dense"
                sx={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 1,
                    pb: 1,
                    [theme.breakpoints.down('sm')]: {
                        flexDirection: 'column',
                        alignItems: 'stretch',
                    },
                }}
            >
                {/* Tournament URL */}
                <Stack direction="row" gap={1} alignItems="center" sx={{flex: 1, minWidth: 200}}>
                    <TextField
                        size="small"
                        variant="outlined"
                        placeholder={i18n.t("tournament_url")}
                        value={tournamentUrl}
                        onChange={(e) => setTournamentUrl(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') loadTournament(); }}
                        sx={(theme) => ({
                            flex: 1,
                            '& .MuiOutlinedInput-root': {
                                color: 'inherit',
                                '& fieldset': { borderColor: theme.palette.divider },
                            },
                            '& .MuiInputBase-input::placeholder': {
                                color: theme.palette.text.disabled,
                            },
                        })}
                        InputProps={{
                            style: { color: 'inherit', fontSize: '0.85rem' }
                        }}
                    />
                    <Button
                        variant="contained"
                        size="small"
                        onClick={loadTournament}
                        loading={loadPending}
                        sx={{whiteSpace: 'nowrap'}}
                    >
                        {i18n.t("load_tournament")}
                    </Button>
                </Stack>

                {/* Auto-update toggle */}
                <Stack direction="row" gap={1} alignItems="center">
                    <FormControlLabel
                        control={
                            <Switch
                                size="small"
                                checked={autoUpdate}
                                onChange={(e) => setAutoUpdate(e.target.checked)}
                            />
                        }
                        label={
                            <Typography variant="body2">{i18n.t("auto_update")}</Typography>
                        }
                        sx={{mr: 0}}
                    />
                    <TextField
                        size="small"
                        variant="outlined"
                        type="number"
                        value={autoUpdateInterval}
                        onChange={(e) => setAutoUpdateInterval(Math.max(1, parseInt(e.target.value) || 1))}
                        sx={(theme) => ({
                            width: 70,
                            '& .MuiOutlinedInput-root': {
                                color: 'inherit',
                                '& fieldset': { borderColor: theme.palette.divider },
                            },
                        })}
                        InputProps={{
                            style: { color: 'inherit', fontSize: '0.85rem' },
                            inputProps: { min: 1 }
                        }}
                    />
                    <Typography variant="body2">s</Typography>
                </Stack>
            </Toolbar>
            </Collapse>
        </AppBar>
    )
}
