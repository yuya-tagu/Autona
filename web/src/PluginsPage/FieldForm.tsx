import React from "react";
import {
    Button,
    Checkbox,
    FormControlLabel,
    MenuItem,
    Stack,
    TextField,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {
    ChoiceLoader,
    FormValues,
    PickValue,
    fieldKind,
    isPickSource,
    isPickValue,
    validateField,
} from "./formFields";
import {InputChoice, Pick, PluginField} from "./pluginTypes";
import {pluginText} from "./pluginText";

// The form drawn from an array of fields (ADR-009 B.3): text / textarea /
// number / bool / select / file / folder / secret, and the source types'
// choices. Controlled: the page owns `values` (formFields.ts's initialValues)
// and validates with validateAll. The job form, the settings and the action
// dialog (I3) all use this one.

export interface FieldFormProps {
    fields: PluginField[];
    values: FormValues;
    onChange: (values: FormValues) => void;
    language: string;
    // Show each invalid field's reason (after a first try to submit).
    showErrors?: boolean;
    disabled?: boolean;
    // Source keys the trigger fills (an action's context): a note, no choice.
    filled?: string[];
    // The manual choices of a source type.
    loadChoices?: ChoiceLoader;
    // The shell requests (the page's api): null when the dialog is closed.
    pickFile?: (extensions: string[]) => Promise<Pick | null>;
    pickFolder?: () => Promise<Pick | null>;
    // Secrets: whether one is saved, and the buttons that clear it.
    secretSet?: Record<string, boolean>;
    onClearSecret?: (key: string) => void;
    // A refusal of the pick (the dialog could not open).
    onPickError?: (error: unknown) => void;
    testIdPrefix?: string;
}

function ChoiceSelect({field, value, label, language, disabled, loadChoices,
                       error, testId, onPick}: {
    field: PluginField;
    value: string;
    label: string;
    language: string;
    disabled: boolean;
    loadChoices?: ChoiceLoader;
    error: string | null;
    testId: string;
    onPick: (id: string) => void;
}) {
    const [choices, setChoices] = React.useState<InputChoice[] | null>(null);
    const [failed, setFailed] = React.useState(false);
    React.useEffect(() => {
        let live = true;
        if (!loadChoices) {
            setChoices([]);
            return undefined;
        }
        loadChoices(field.type).then((rows) => {
            if (live) {
                setChoices(rows);
            }
        }, () => {
            if (live) {
                setFailed(true);
                setChoices([]);
            }
        });
        return () => {
            live = false;
        };
    }, [field.type, loadChoices]);
    const help = field.help ? pluginText(field.help, language) : "";
    return (
        <TextField select size="small" label={label} value={value}
                   required={field.required} disabled={disabled}
                   error={error !== null}
                   helperText={error
                       ? i18n.t(`plugins_err_${error}`)
                       : failed ? i18n.t("plugins_choices_failed")
                           : choices?.length === 0
                               ? i18n.t("plugins_choices_none") : help}
                   inputProps={{"data-testid": testId}}
                   onChange={(e) => onPick(e.target.value)}>
            <MenuItem value="">{i18n.t("plugins_choice_unset")}</MenuItem>
            {(choices ?? []).map((choice) => (
                <MenuItem key={choice.id} value={choice.id}>
                    {choice.label}
                </MenuItem>
            ))}
        </TextField>
    );
}

export default function FieldForm({
    fields, values, onChange, language, showErrors = false, disabled = false,
    filled = [], loadChoices, pickFile, pickFolder, secretSet = {},
    onClearSecret, onPickError, testIdPrefix = "plugin-field",
}: FieldFormProps) {
    const set = (key: string, value: unknown) => onChange(
        {...values, [key]: value});

    const pick = async (field: PluginField) => {
        try {
            const picked = field.type === "file"
                ? await pickFile?.(field.extensions ?? [])
                : await pickFolder?.();
            if (picked) {
                const held = values[field.key];
                const stored = isPickValue(held) ? held.stored : null;
                set(field.key, {pick: picked, stored} satisfies PickValue);
            }
        } catch (error) {
            onPickError?.(error);
        }
    };

    return (
        <Stack spacing={2}>
            {fields.map((field) => {
                const value = values[field.key];
                const label = pluginText(field.label, language);
                const help = field.help ? pluginText(field.help, language) : "";
                const reason = showErrors
                    ? validateField(field, value, Boolean(secretSet[field.key]))
                    : null;
                const testId = `${testIdPrefix}-${field.key}`;
                const kind = fieldKind(field);

                if (kind === "unknown") {
                    return (
                        <Typography key={field.key} variant="body2"
                                    color="error" data-testid={testId}>
                            {i18n.t("plugins_err_unknown_type",
                                    {label, type: field.type})}
                        </Typography>
                    );
                }
                if (kind === "source") {
                    if (field.type === "state") {
                        return (
                            <Typography key={field.key} variant="body2"
                                        color="text.secondary"
                                        data-testid={testId}>
                                {i18n.t("plugins_source_state", {label})}
                            </Typography>
                        );
                    }
                    if (!isPickSource(field) || filled.includes(field.key)) {
                        return (
                            <Typography key={field.key} variant="body2"
                                        color="text.secondary"
                                        data-testid={testId}>
                                {i18n.t("plugins_source_context", {label})}
                            </Typography>
                        );
                    }
                    return (
                        <ChoiceSelect key={field.key} field={field}
                                      value={typeof value === "string"
                                          ? value : ""}
                                      label={label} language={language}
                                      disabled={disabled}
                                      loadChoices={loadChoices} error={reason}
                                      testId={testId}
                                      onPick={(id) => set(field.key, id)}/>
                    );
                }

                switch (field.type) {
                    case "bool":
                        return (
                            <FormControlLabel
                                key={field.key} label={label}
                                control={
                                    <Checkbox checked={Boolean(value)}
                                              disabled={disabled}
                                              inputProps={{
                                                  "aria-label": label,
                                                  "data-testid": testId,
                                              } as React.InputHTMLAttributes<
                                                  HTMLInputElement>}
                                              onChange={(e) => set(
                                                  field.key, e.target.checked)}/>
                                }/>
                        );
                    case "select":
                        return (
                            <TextField key={field.key} select size="small"
                                       label={label}
                                       value={typeof value === "string"
                                           ? value : ""}
                                       required={field.required}
                                       disabled={disabled}
                                       error={reason !== null}
                                       helperText={reason
                                           ? i18n.t(`plugins_err_${reason}`)
                                           : help}
                                       inputProps={{"data-testid": testId}}
                                       onChange={(e) => set(
                                           field.key, e.target.value)}>
                                {field.required ? null : (
                                    <MenuItem value="">
                                        {i18n.t("plugins_choice_unset")}
                                    </MenuItem>
                                )}
                                {(field.options ?? []).map((option) => (
                                    <MenuItem key={option.value}
                                              value={option.value}>
                                        {pluginText(option.label, language)}
                                    </MenuItem>
                                ))}
                            </TextField>
                        );
                    case "file":
                    case "folder": {
                        const held = isPickValue(value) ? value : null;
                        const name = held?.pick?.name || held?.stored || "";
                        return (
                            <Stack key={field.key} spacing={0.5}
                                   data-testid={testId}>
                                <Stack direction="row" spacing={1}
                                       alignItems="center">
                                    <Typography variant="body2"
                                                sx={{minWidth: 120}}>
                                        {label}{field.required ? " *" : ""}
                                    </Typography>
                                    <Button size="small" variant="outlined"
                                            disabled={disabled}
                                            onClick={() => void pick(field)}>
                                        {i18n.t(field.type === "file"
                                            ? "plugins_pick_file"
                                            : "plugins_pick_folder")}
                                    </Button>
                                    <Typography variant="body2"
                                                data-testid={`${testId}-name`}>
                                        {name || i18n.t("plugins_pick_none")}
                                    </Typography>
                                </Stack>
                                <Typography variant="caption"
                                            color={reason ? "error"
                                                : "text.secondary"}>
                                    {reason ? i18n.t(`plugins_err_${reason}`)
                                        : help}
                                </Typography>
                            </Stack>
                        );
                    }
                    case "secret": {
                        const isSet = Boolean(secretSet[field.key]);
                        return (
                            <Stack key={field.key} direction="row" spacing={1}
                                   alignItems="flex-start">
                                <TextField size="small" type="password"
                                           autoComplete="new-password"
                                           label={label} sx={{flex: 1}}
                                           value={typeof value === "string"
                                               ? value : ""}
                                           required={field.required && !isSet}
                                           disabled={disabled}
                                           error={reason !== null}
                                           placeholder={isSet
                                               ? i18n.t("plugins_secret_set")
                                               : ""}
                                           helperText={reason
                                               ? i18n.t(`plugins_err_${reason}`)
                                               : isSet
                                                   ? i18n.t("plugins_secret_set")
                                                   : help}
                                           inputProps={{"data-testid": testId}}
                                           onChange={(e) => set(
                                               field.key, e.target.value)}/>
                                {isSet && onClearSecret ? (
                                    <Button size="small" color="error"
                                            disabled={disabled}
                                            onClick={() => onClearSecret(
                                                field.key)}>
                                        {i18n.t("plugins_secret_clear")}
                                    </Button>
                                ) : null}
                            </Stack>
                        );
                    }
                    default: {
                        const multiline = field.type === "textarea";
                        const numeric = field.type === "number";
                        return (
                            <TextField key={field.key} size="small"
                                       label={label} multiline={multiline}
                                       minRows={multiline ? 3 : undefined}
                                       type={numeric ? "number" : "text"}
                                       value={typeof value === "string"
                                           ? value : ""}
                                       required={field.required}
                                       disabled={disabled}
                                       error={reason !== null}
                                       helperText={reason
                                           ? i18n.t(`plugins_err_${reason}`,
                                                    {min: field.min,
                                                     max: field.max,
                                                     step: field.step})
                                           : help}
                                       inputProps={numeric
                                           ? {"data-testid": testId,
                                              min: field.min, max: field.max,
                                              step: field.step}
                                           : {"data-testid": testId}}
                                       onChange={(e) => set(
                                           field.key, e.target.value)}/>
                        );
                    }
                }
            })}
        </Stack>
    );
}
