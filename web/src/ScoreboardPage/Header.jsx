import {
    AppBar,
    Button,
    Checkbox,
    FormControlLabel,
    IconButton,
    MenuItem,
    Menu,
    Select,
    Switch,
    TextField,
    Toolbar,
    Typography
} from "@mui/material";
import {Box, Stack} from "@mui/system";
import {useTheme} from "@mui/material/styles";
import {shallowEqual, useDispatch, useSelector} from "react-redux";
import {BASE_URL, BACKEND_PORT} from "../env";
import {GameIcon} from "../GameIcon";
import i18n from "../i18n/config";
import React from "react";
import {Visibility} from "@mui/icons-material";
import {visibilitySlice} from "../redux/uiState";


export const Header = ({onSelectedGameChange, scoreboardNumber = 1, ...rest}) => {
    const {tshState, games, visibility} = useSelector(state => ({
      tshState: state.tshState.tshState,
      games: state.tshGames.value,
      visibility: state.visibility,
    }), shallowEqual);

    const dispatch = useDispatch();
    const theme = useTheme();

    const [tournamentUrl, setTournamentUrl] = React.useState("");
    const [autoUpdate, setAutoUpdate] = React.useState(false);
    const [autoUpdateInterval, setAutoUpdateInterval] = React.useState(10);
    const autoUpdateTimerRef = React.useRef(null);
    const [visMenuAnchor, setVisMenuAnchor] = React.useState(null);

    const loadTournament = () => {
        if (!tournamentUrl) return;
        fetch(`http://${window.location.hostname}:${BACKEND_PORT}/set-tournament?url=${encodeURIComponent(tournamentUrl)}`)
            .then(resp => resp.text())
            .then(d => console.log("Load tournament:", d))
            .catch(console.error);
    };

    const pullStreamSet = () => {
        fetch(`http://${window.location.hostname}:${BACKEND_PORT}/scoreboard${scoreboardNumber}-pull-stream`)
            .catch(console.error);
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

    const visibilityItems = [
        {key: "twitter",    label: i18n.t("show_twitter")},
        {key: "seed",       label: i18n.t("show_seed")},
        {key: "location",   label: i18n.t("show_location")},
        {key: "characters", label: i18n.t("show_characters")},
        {key: "pronouns",   label: i18n.t("show_pronouns")},
        {key: "additional", label: i18n.t("show_additional")},
    ];

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
                    flexDirection: 'column',
                    gap: 1,
                    paddingY: 2,
                },
            }}>

                <Typography
                    variant="h6"
                    gap={2}
                    sx={{
                        display: 'flex',
                        alignItems: "center",
                        mr: 2,
                    }}
                >
                    <img alt="TSH logo" src={`${BASE_URL}/assets/icons/icon.png`} height={48} width={48} sx={{mr: 2}} />
                    Web Scoreboard
                </Typography>

                {/* Game selector */}
                <Box
                    sx={{
                        display: 'flex',
                        alignItems: 'center',
                        mr: 2
                    }}
                >
                    <span>Game:&nbsp;</span>
                    <Select
                      size="small"
                      sx={{
                          '.MuiSelect-select': {
                              paddingY: 0,
                              marginY: 0
                          },
                      }}
                      value={tshState.game.codename}
                      renderValue={(codename) =>
                        <GameIcon game={games[codename]} />
                      }
                      id={"header-game-select"}
                      onChange={(e) => onSelectedGameChange(e.target.value)}
                    >
                        {
                            Object.values(games).map(game =>
                              <MenuItem key={game.codename} value={game.codename}>
                                  <GameIcon fixedWidth={true} game={game} />
                                  <Typography>{game.name}</Typography>
                              </MenuItem>
                            )
                        }
                    </Select>
                </Box>

                {/* Event name */}
                <Box sx={{
                    overflowX: "hidden",
                    textOverflow: "ellipsis",
                    maxWidth: '100%',
                    mr: 2,
                }}>
                    <span>Event:&nbsp;</span>
                    <span>{tshState.tournamentInfo.tournamentName}:</span>
                    <span>{tshState.tournamentInfo.eventName}</span>
                </Box>

                {/* Visibility toggle button */}
                <IconButton
                    color="inherit"
                    size="small"
                    onClick={(e) => setVisMenuAnchor(e.currentTarget)}
                    title={i18n.t("visibility_settings")}
                    sx={{ml: 'auto'}}
                >
                    <Visibility />
                </IconButton>
                <Menu
                    anchorEl={visMenuAnchor}
                    open={Boolean(visMenuAnchor)}
                    onClose={() => setVisMenuAnchor(null)}
                >
                    {visibilityItems.map(item => (
                        <MenuItem key={item.key} onClick={() => dispatch(visibilitySlice.actions.toggle(item.key))} dense>
                            <FormControlLabel
                                control={<Checkbox checked={visibility[item.key]} size="small" />}
                                label={item.label}
                                sx={{pointerEvents: 'none'}}
                            />
                        </MenuItem>
                    ))}
                </Menu>
            </Toolbar>

            {/* Second toolbar row: Tournament URL + Auto-update */}
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
        </AppBar>
    )
}
