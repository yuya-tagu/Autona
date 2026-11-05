import TextField from "./ScoreboardPage/TextField";
import i18n from "./i18n/config";
import React from "react";
import {Autocomplete, createFilterOptions, InputAdornment} from "@mui/material";
import {BACKEND_PORT, inlineFlagWidth} from "./env";
import {useSelector} from "react-redux";

/**
 * @param {?string} countryCode
 * @param {(val: string)=> any} onChange
 * @param {?Object} countries the rows to offer, by code, instead of the list
 *   of assets/data_countries.json. GET /api/v1/countries has the custom flags
 *   that list does not: a row says its text as `label` and its flag's URL as
 *   `flag`.
 * @param props
 * @returns {Element}
 */
export function CountrySelector({
    value,
    onChange,
    countries: givenCountries,
    ...props
}) {
    const storedCountries = useSelector(state => state.autonaCountries.value);
    const countries = givenCountries ?? storedCountries;

    const country = countries[value];
    const getCountryFlagAsset = (inp) => {
        let c = typeof inp === 'string'
            ? countries[inp]
            : inp;

        if (c.flag) {
            return c.flag;
        }

        if (!c.code || !c.code.toLowerCase) {
            return null;
        }

        return `http://${window.location.hostname}:${BACKEND_PORT}/assets/country_flag/${c.code.toLowerCase()}.png`
    };

    const countryDisplayName = (/** AutonaCountry */ country) => {
        if (country?.label) {
            return country.label;
        }
        return country?.name ? `${country.name} (${country.code})` : country;
    };

    return <Autocomplete
        autoHighlight
        autoComplete
        autoSelect
        forcePopupIcon={false}
        clearIcon={null}
        filterOptions={(options, state) => {
            if (state.inputValue.length < 2) {
                return [];
            }

            return createFilterOptions({
                limit: 8,
            })(options, state);
        }}
        renderOption={(_props, option, _) => {
            const {key, ...rest} = _props;
            const flagAsset = getCountryFlagAsset(option);
            return (
                <li key={key} {...rest}>
                    { flagAsset &&
                        <img alt="Country flag" loading={"lazy"} src={flagAsset} width={inlineFlagWidth} />
                    }
                    <span style={{marginLeft: '16px'}}>
                        {countryDisplayName(option)}
                    </span>
                </li>
            )
        }}
        renderInput={(params) => {
            const slotProps = params.slotProps ?? {};

            if (country?.name) {
                const flagAsset = getCountryFlagAsset(country);
                if (flagAsset) {
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

            return <TextField
                {...params}
                slotProps={slotProps}
                label={i18n.t("country")}
                size="small"
            />
        }}

        value={country ?? null}
        options={Object.values(countries)}
        onChange={(ev, val) => onChange(val?.code ?? "")}
        getOptionLabel={(opt) => (opt?.label ?? opt?.name ?? "")}
        {...props}
        sx={{
            ...(props.sx ?? {}),
            '& .MuiAutocomplete-popupIndicator': {
                display: 'none'
            },
        }}
    />
}
