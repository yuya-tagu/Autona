import React from "react";
import {
    Button, Card, CardContent, CardHeader,
    Checkbox, Chip, Collapse,
    FormControlLabel,
    FormGroup, IconButton,
    Paper,
    Skeleton,
    Stack,
    Table, TableBody, TableCell,
    TableContainer,
    TableHead, TableRow,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import TextField from "./TextField";
import {alpha} from "@mui/material/styles";
import {ExpandMore} from "@mui/icons-material";
import {apiGet, apiGetJson} from "../api";
import {showSnackbar} from "./Feedback";

// get-sets / load-set proxy Start.gg queries on the desktop side, which can
// legitimately take tens of seconds (20s GQL timeout + retries).
const SETS_TIMEOUT_MS = 60000;


/**
 * @typedef {object} UpcomingSetsState
 * @property {?string} setFilter
 * @property {?TSHSet} selectedSet
 * @property {boolean} showFinished
 * @property {boolean} expanded
 * @property {Array.<TSHSet>} sets
 */
export default class UpcomingSets extends React.Component {
    /** @type UpcomingSetsState */ state;

    constructor(props) {
        super(props);

        this.state = {
            isLoading: true,
            setFilter: null,
            selectedSet: null,
            showFinished: false,
            expanded: true,
            sets: []
        };
    }

    componentDidMount = () => {
        this.FetchSets();
    }

    FetchSets = () => {
        this.setState({isLoading: true});

        apiGetJson(
            `/get-sets?_foo=${this.state.showFinished ? "&getFinished=true" : ""}&`,
            {timeoutMs: SETS_TIMEOUT_MS}
        )
            .then(( /** {Array.<tourney_set>} */ data) => {
                this.setState({isLoading: false, sets: data});
            })
            .catch((e) => {
                console.error(e);
                showSnackbar(i18n.t("request_failed"));
                this.setState({isLoading: false});
            });
    }

    SubmitNewSet = () => {
        this.setState({isLoading: true});

        apiGet(
            `/scoreboard${this.props.scoreboardNumber}-load-set?set=${this.state.selectedSet?.id}`,
            {timeoutMs: SETS_TIMEOUT_MS}
        )
            .then(() => {
                this.setState({isLoading: false});
                this.props.onSelectedSetChanged && this.props.onSelectedSetChanged(this.state.selectedSet.id);
            })
            .catch((e) => {
                console.error(e);
                showSnackbar(i18n.t("request_failed"));
                this.setState({isLoading: false});
            });
    }

    showFinishedSetsChanged = (e) => {
        this.setState({
            showFinished: e.target.checked ?? false
        }, () => {
            this.FetchSets();
        });
    }

    filterFieldChanged = (e) => {
        const newVal = e.target.value === "" ? null : e.target.value;
        this.setState({
            setFilter: newVal
        });
    }

    /**
     * @returns {TSHSet[]}
     */
    filteredSets = () => {
        if (this.state.setFilter === null) {
            return this.state.sets;
        }

        const fs = this.state.setFilter.toLocaleLowerCase();

        return this.state.sets.filter((set) => (
            set.p1_name.toLocaleLowerCase().includes(fs)
            || set.p2_name.toLocaleLowerCase().includes(fs)
            || set.round_name.toLocaleLowerCase().includes(fs)
            || set.tournament_phase?.toLocaleLowerCase().includes(fs)
            || set.tournament_pool?.toLocaleLowerCase().includes(fs)
            || set.stream?.toLocaleLowerCase().includes(fs)
        ));
    }

    onSetClicked = ( /** @type {TSHSet} */ set) => {
        this.setState({
            selectedSet: set
        });
    }

    render = () => {
        return (
            <Card>
                <CardHeader
                    title={`Upcoming Sets`}
                    action={
                        <IconButton size="small" onClick={() => this.setState({...this.state, expanded: !this.state.expanded})}>
                            <ExpandMore sx={{
                                transform: this.state.expanded ? 'rotate(0deg)' : 'rotate(270deg)'
                            }} />
                        </IconButton>
                    }
                />
                <Collapse in={this.state.expanded} timeout={"auto"}>
                    <CardContent>
                        <Stack xs={12} spacing={2}>
                            <Stack direction={"row"} gap={2}>
                                <Button
                                    variant={"outlined"}
                                    size="small"
                                    onClick={() => this.FetchSets()}
                                    disabled={this.state.isLoading}
                                >
                                    {i18n.t("refresh_sets")}
                                </Button>

                                <Button
                                    variant={"outlined"}
                                    size="small"
                                    onClick={() => this.SubmitNewSet()}
                                    loading={this.state.isLoading}
                                    disabled={!this.state.selectedSet}
                                    sx={{display: {xs: 'none', sm: 'inline-flex'}}}
                                >
                                    {i18n.t("load_selected_set")}
                                </Button>
                            </Stack>

                            <FormGroup>
                                <FormControlLabel
                                    control={
                                        <Checkbox id={"show-finished-sets"} size="small" label="Show finished sets" onChange={this.showFinishedSetsChanged}  />
                                    }
                                    label={i18n.t("show_finished_sets")}
                                />
                                <TextField label={i18n.t("filter")}
                                           onChange={this.filterFieldChanged}
                                           variant="outlined"
                                           sx={(theme) => ({
                                               '& label[data-shrink="false"]': {color: theme.palette.text.disabled}
                                           })}
                                />
                            </FormGroup>

                            {/* Phone (xs): card list — the 6-column table is
                                unusable at 375px. sm+ keeps the table. */}
                            <Stack gap={1} sx={{display: {xs: 'flex', sm: 'none'}}}>
                                {this.state.isLoading && [0, 1, 2].map((i) => (
                                    <Skeleton key={`card-skeleton-${i}`} variant="rounded" height={72} />
                                ))}
                                {!this.state.isLoading && this.filteredSets().map((/** TSHSet */ set) => (
                                    <Paper
                                        key={`card-${set.id}`}
                                        onClick={() => {this.onSetClicked(set)}}
                                        sx={(theme) => ({
                                            padding: 1.5,
                                            cursor: 'pointer',
                                            border: this.state.selectedSet?.id === set.id
                                                ? `1px solid ${theme.palette.primary.main}`
                                                : `1px solid ${theme.palette.divider}`,
                                            backgroundColor: this.state.selectedSet?.id === set.id
                                                ? alpha(theme.palette.primary.main, theme.palette.action.selectedOpacity)
                                                : undefined,
                                        })}
                                    >
                                        <Typography variant="body2" fontWeight={700}>
                                            {set.p1_name} vs {set.p2_name}
                                        </Typography>
                                        <Stack direction="row" gap={0.5} flexWrap="wrap" marginTop={0.5}>
                                            {set.tournament_phase && <Chip size="small" label={set.tournament_phase} />}
                                            {set.tournament_pool && <Chip size="small" label={set.tournament_pool} />}
                                            {set.round_name && <Chip size="small" variant="outlined" label={set.round_name} />}
                                            {set.stream && <Chip size="small" color="secondary" label={set.stream} />}
                                            {set.station && <Chip size="small" variant="outlined" label={`#${set.station}`} />}
                                        </Stack>
                                    </Paper>
                                ))}
                                <Button
                                    fullWidth
                                    variant="contained"
                                    sx={{minHeight: 40}}
                                    onClick={() => this.SubmitNewSet()}
                                    loading={this.state.isLoading}
                                    disabled={!this.state.selectedSet}
                                >
                                    {i18n.t("load_selected_set")}
                                </Button>
                            </Stack>

                            <TableContainer sx={{display: {xs: 'none', sm: 'block'}}}>
                                <Table stickyHeader>
                                    <TableHead>
                                        <TableRow>
                                            <TableCell>{i18n.t("p1")}</TableCell>
                                            <TableCell>{i18n.t("p2")}</TableCell>
                                            <TableCell>{i18n.t("phase")}</TableCell>
                                            <TableCell>{i18n.t("pool")}</TableCell>
                                            <TableCell>{i18n.t("match")}</TableCell>
                                            <TableCell>{i18n.t("stream")}</TableCell>
                                            <TableCell>{i18n.t("station")}</TableCell>
                                        </TableRow>
                                    </TableHead>
                                    <TableBody>
                                        {
                                            this.state.isLoading &&
                                            [0, 1, 2].map((i) => (
                                                <TableRow key={`skeleton-${i}`}>
                                                    {[0, 1, 2, 3, 4, 5, 6].map((j) => (
                                                        <TableCell key={j}><Skeleton /></TableCell>
                                                    ))}
                                                </TableRow>
                                            ))
                                        }
                                        {
                                            !this.state.isLoading &&
                                            this.filteredSets().map((/** TSHSet */ set) => (
                                                <TableRow
                                                    hover
                                                    onClick={() => {this.onSetClicked(set)}}
                                                    key={set.id}
                                                    sx={(theme) => ({
                                                        '&:last-child td, &:last-child th': {
                                                            border: 0
                                                        },
                                                        'td': (this.state.selectedSet?.id !== set.id ? {} : {
                                                            backgroundColor: theme.vars
                                                                ? `rgba(${theme.vars.palette.primary.mainChannel} / calc(${theme.vars.palette.action.selectedOpacity} + ${theme.vars.palette.action.hoverOpacity}))`
                                                                : alpha(
                                                                    theme.palette.primary.main,
                                                                    theme.palette.action.selectedOpacity,
                                                                ),
                                                        })
                                                    })}
                                                >
                                                    {/*
                                          * It's unclear if the backend will translate things like
                                          * "Losers Round 5" for us. Stuff exists to support it in the
                                          * python side of things, but using it browser-side is definitely
                                          * a yak that needs shaving.
                                          */}
                                                    <TableCell>{set.p1_name}</TableCell>
                                                    <TableCell>{set.p2_name}</TableCell>
                                                    <TableCell>{set.tournament_phase}</TableCell>
                                                    <TableCell>{set.tournament_pool}</TableCell>
                                                    <TableCell>{set.round_name}</TableCell>
                                                    <TableCell>{set.stream}</TableCell>
                                                    <TableCell>{set.station}</TableCell>
                                                </TableRow>
                                            ))
                                        }
                                    </TableBody>
                                </Table>
                            </TableContainer>
                        </Stack>
                    </CardContent>
                </Collapse>
            </Card>
        )
    }
}
