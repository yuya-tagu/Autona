import TextField from "./ScoreboardPage/TextField";
import i18n from "./i18n/config";
import React, {useEffect, useState} from "react";
import {Autocomplete, InputAdornment} from "@mui/material";
import {BACKEND_PORT, inlineFlagWidth} from "./env";
import {apiGetJson} from "./api";

/**
 * @param {?string} countryCode
 * @param {?string} value
 * @param {(val: string)=> any} onChange
 * @param props
 * @returns {Element}
 */
export function CountryStateSelector({
                                    countryCode,
                                    value,
                                    onChange,
                                    ...props
                                }) {
    const isValidCountryCode = (countryCode !== "" && !!countryCode);
    const [stateData, setStateData] = useState({
        states: {},
        isLoading: true
    });

    const selectedState = (!stateData.isLoading && stateData.states[value])
        ? stateData.states[value]
        : undefined;

    const stateDisplayName = (state) => {
        return `${state.name} (${state.code})`;
    }

    // Not while the regions are on their way: Tab from the country field lands
    // here as the country changes, and a disabled field would lose the focus.
    const isDisabled = !isValidCountryCode;

    useEffect(() => {
        if (!isValidCountryCode) {
            return undefined;
        }
        // Another country's regions are not offered while this one's are on
        // their way, and its answer arriving late does not replace them.
        let live = true;
        setStateData({states: {}, isLoading: true});
        apiGetJson(`/api/v1/countries/${encodeURIComponent(countryCode)}/states`)
            .then(({data}) => {
                if (live) {
                    setStateData({states: data ?? {}, isLoading: false});
                }
            })
            .catch(e => {
                console.warn(`Failed to load autocomplete state data for country code ${countryCode}`, e);
                if (live) {
                    setStateData({states: {}, isLoading: false});
                }
            });
        return () => {
            live = false;
        };
    }, [countryCode, isValidCountryCode]);

    const getStateFlagAsset = (inp) => {
        const stData = typeof inp === 'string'
            ? stateData.states[inp]
            : inp;

        if (!stData) { return null; }

        let stateAsset = stData?.asset;
        if (stateAsset && stateAsset[0] === ".") {
            return `http://${window.location.hostname}:${BACKEND_PORT}${stateAsset.slice(1)}`;
        } else {
            return `http://${window.location.hostname}:${BACKEND_PORT}/assets/state_flag/${countryCode}/${stData.code}.png`;
        }
    }

    return <Autocomplete
        autoHighlight
        forcePopupIcon={false}
        clearIcon={null}
        disabled={isDisabled}
        loading={stateData.isLoading}
        loadingText={i18n.t("loading")}
        renderOption={(_props, option, _) => {
            const {key, ...rest} = _props;
            const stateFlagAsset = getStateFlagAsset(option);
            const optionState = stateData.states[option];

            return (
                <li key={key} {...rest}>
                    { stateFlagAsset &&
                        <img
                            alt="State flag"
                            loading={"lazy"}
                            src={stateFlagAsset}
                            width={inlineFlagWidth}
                            onError={(e) => e.nativeEvent.target.remove()}
                            style={{marginRight: '16px'}} />
                    }
                    <span>
                        {stateDisplayName(optionState) ?? option}
                    </span>
                </li>
            )
        }}
        renderInput={(params) => {
            const slotProps = params.slotProps ?? {};
            if (selectedState?.name) {
                const flagAsset = getStateFlagAsset(selectedState);
                if (flagAsset && !isDisabled) {
                    // slotProps.input replaces InputProps, and without its ref
                    // the options have no anchor: they would not open.
                    slotProps['input'] = {
                        ...params.InputProps,
                        startAdornment: <InputAdornment position="start">
                            <img
                                alt="Country flag"
                                loading={"lazy"}
                                src={flagAsset}
                                onError={(e) => e.nativeEvent.target.remove()}
                                width={inlineFlagWidth}
                            />
                        </InputAdornment>
                    }
                }
            }

            return <div>
                <TextField
                    {...params}
                    slotProps={slotProps}
                    label={i18n.t("state")}
                    size="small"
                />
            </div>
        }}

        value={isDisabled ? null : (selectedState?.name ?? null)}
        options={Object.keys(stateData.states)}
        onChange={(ev, val) => onChange(stateData?.states[val]?.code ?? "")}
        getOptionLabel={(opt) => (stateData.states[opt]?.name ?? opt)}
        {...props}
        sx={{
            ...(props.sx ?? {}),
            '& .MuiAutocomplete-popupIndicator': {
                display: 'none'
            },
        }}
    />
}
