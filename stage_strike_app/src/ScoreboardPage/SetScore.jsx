import React from 'react';
import {Stack, Typography} from "@mui/material";
import i18n from "i18next";
import {BACKEND_PORT} from "../env";
import {NumberInput} from "../NumberInput";

/**
 * @typedef {object} SetScoreState
 * @prop {number} scoreLeft
 * @prop {number} scoreRight
 * @prop {boolean} isLoading
 * @prop {?number} bestOf
 * @prop {string} match
 * @prop {string} phase
 */

export default class SetScore extends React.Component {
    /** @type {SetScoreState} */
    state;

    /**
     * @param {TSHTeamInfo} props.leftTeam
     * @param {TSHTeamInfo} props.rightTeam
     * @param {number=} props.best_of
     * @param {string=} props.match
     * @param {string=} props.phase
     */
    constructor(props) {
        super(props);
        this.state = this.stateFromProps(props);
    }

    /** @returns {SetScoreState} */
    stateFromProps(props) {
        const best_of = (
            (props.best_of !== undefined)
                ? props.best_of
                : null
        );

        return {
            scoreLeft: props.leftTeam.score,
            scoreRight: props.rightTeam.score,
            match: props.match ?? "",
            phase: props.phase ?? "",
            bestOf: best_of
        };
    }

    componentDidUpdate(prevProps, prevState, snapshot) {
        if (JSON.stringify(prevProps) !== JSON.stringify(this.props)) {
            // console.log("Set score component received new props.", prevProps, this.props);
            this.setState({...prevState, ...this.stateFromProps(this.props)});
        }
    }

    /** @return {?number} */
    maxWins = () => {
        let bestOf = parseInt(this.state.bestOf);
        if (isNaN(bestOf) || isNaN(bestOf - 0)) {
            bestOf = null;
        }

        return bestOf
            ? (Math.floor(bestOf / 2) + 1)
            : null;
    }

    submitScore = (scoreboardNumber) => {
        return (
            fetch(`http://${window.location.hostname}:${BACKEND_PORT}/score`,
                {
                    method: 'POST',
                    headers: {'content-type': 'application/json'},
                    body: JSON.stringify({
                        scoreboard: scoreboardNumber,
                        team1score: Number.parseInt(this.state.scoreLeft),
                        team2score: Number.parseInt(this.state.scoreRight)
                    })
                })
                .then(resp => resp.text())
                .then((d) => console.log("Submit Score: ", d))
        );
    }

    submitSetInfo = (scoreboardNumber) => {
        return (
            fetch(`http://${window.location.hostname}:${BACKEND_PORT}/scoreboard${scoreboardNumber}-set?` + new URLSearchParams({
                "best-of": this.state.bestOf,
                "phase": this.state.phase,
                "match": this.state.match,
            }).toString())
                .then(resp => resp.text())
                .then((d) => console.log("Submit set info: ", d))
        )
    }

    render = () => {
        const formFieldWidth = 195;
        return (
            <Stack gap={1.5} direction={"column"} alignItems={"center"}>
                {(this.state.phase || this.state.match) && (
                    <Stack direction="row" spacing={1} alignItems="center">
                        {this.state.phase && (
                            <Typography variant="body2" color="text.secondary">
                                {this.state.phase}
                            </Typography>
                        )}
                        {this.state.phase && this.state.match && (
                            <Typography variant="body2" color="text.secondary">-</Typography>
                        )}
                        {this.state.match && (
                            <Typography variant="body2" color="text.secondary">
                                {this.state.match}
                            </Typography>
                        )}
                    </Stack>
                )}
                <NumberInput
                    type={"number"}
                    label={i18n.t("best_of", {value: ""})}
                    variant="outlined"
                    wingWidth={48}
                    value={this.state.bestOf}
                    onChange={((_, newVal) => {this.setState({...this.state, bestOf: newVal})})}
                    width={formFieldWidth}
                    min={0}
                />
                <Stack gap={0.5} direction={"row"} alignItems={"baseline"}>
                    <NumberInput
                        value={this.state.scoreLeft}
                        onChange={((_, newVal) => {this.setState({...this.state, scoreLeft: newVal})})}
                        min={0}
                        max={this.maxWins()}
                    />
                    <span>-</span>
                    <NumberInput
                        value={this.state.scoreRight}
                        onChange={((_, newVal) => {this.setState({...this.state, scoreRight: newVal})})}
                        min={0}
                        max={this.maxWins()}
                    />
                </Stack>
            </Stack>
        )
    }
}
