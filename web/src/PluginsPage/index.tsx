import React from "react";
import {
    Alert,
    Avatar,
    Box,
    Button,
    Chip,
    Stack,
    Typography,
} from "@mui/material";
import i18n from "../i18n/config";
import {FeedbackProvider, showSnackbar} from "../ScoreboardPage/Feedback";
import {usePanelShell} from "../PanelShell/context";
import {useShellAccess} from "../PanelShell/useShellAccess";
import {CardFrame, GuideFrame, Note} from "../GuidePages/parts";
import {PluginsApi, httpPluginsApi} from "./pluginsApi";
import {PluginListItem} from "./pluginTypes";
import {pluginText, sizeLabel, versionLabel} from "./pluginText";
import PluginDetailView from "./PluginDetailView";
import DevSection from "./DevSection";
import InstallSection from "./InstallSection";

// The generic page "Plugins" (ADR-009 D4): the list of what is installed and,
// for the one chosen, its settings, jobs, runs, logs and artifacts. The list
// is THIS_PC (it reads anywhere); everything else is SHELL, so in a browser
// the page only lists (and only on this PC: the list is THIS_PC).

function PluginsContent({api}: {api: PluginsApi}) {
    const {language} = usePanelShell();
    const {access, retry} = useShellAccess({withToken: false});
    const shell = access === "shell";
    const [list, setList] = React.useState<PluginListItem[] | null>(null);
    const [failed, setFailed] = React.useState(false);
    const [selected, setSelected] = React.useState<string | null>(null);

    const read = React.useCallback(() => {
        api.list().then((rows) => {
            setList(rows);
            setFailed(false);
        }, () => setFailed(true));
    }, [api]);
    React.useEffect(read, [read]);

    const onError = React.useCallback((text: string) => {
        showSnackbar(text, "error");
    }, []);

    return (
        <GuideFrame panel="plugins" title={i18n.t("plugins_title")}
                    description={i18n.t("plugins_description")}
                    openInBrowser={false}>
            {access === "failed" ? (
                <Alert severity="error" data-testid="plugin-access-failed"
                       action={
                           <Button color="inherit" size="small" onClick={retry}>
                               {i18n.t("plugins_retry")}
                           </Button>}>
                    {i18n.t("plugins_access_failed")}
                </Alert>
            ) : null}
            {failed ? (
                <Alert severity="error" action={
                    <Button color="inherit" size="small" onClick={read}>
                        {i18n.t("plugins_retry")}
                    </Button>}>
                    {i18n.t("plugins_list_failed")}
                </Alert>
            ) : null}
            <CardFrame title={i18n.t("plugins_list")}>
                {list && list.length === 0 ? (
                    <Note>{i18n.t("plugins_list_empty")}</Note>
                ) : null}
                <Stack spacing={1} data-testid="plugin-list">
                    {(list ?? []).map((item) => (
                        <Box key={item.id} component="button" type="button"
                             data-testid={`plugin-row-${item.id}`}
                             aria-pressed={selected === item.id}
                             disabled={!shell}
                             onClick={() => setSelected(item.id)}
                             sx={{
                                 display: "flex", alignItems: "center", gap: 1.5,
                                 p: 1, textAlign: "left", cursor: "pointer",
                                 font: "inherit", color: "inherit",
                                 bgcolor: selected === item.id
                                     ? "action.selected" : "transparent",
                                 border: 1, borderColor: "divider",
                                 borderRadius: 1,
                                 "&:disabled": {cursor: "default"},
                             }}>
                            <Avatar variant="rounded"
                                    src={item.has_icon
                                        ? api.iconUrl(item.id) : undefined}
                                    alt="">
                                {pluginText(item.name, language).slice(0, 1)}
                            </Avatar>
                            <Box sx={{flex: 1, minWidth: 0}}>
                                <Stack direction="row" spacing={1}
                                       alignItems="center">
                                    <Chip size="small" variant="outlined"
                                          label={i18n.t(
                                              `plugins_badge_${item.badge}`)}/>
                                    <Typography variant="subtitle2" noWrap>
                                        {pluginText(item.name, language)}
                                    </Typography>
                                </Stack>
                                <Typography variant="caption"
                                            color="text.secondary">
                                    {[item.author, versionLabel(item.version),
                                      sizeLabel(item.size_bytes)]
                                        .filter(Boolean).join(" · ")}
                                </Typography>
                            </Box>
                            <Chip size="small"
                                  label={i18n.t(`plugins_state_${item.state}`)}/>
                        </Box>
                    ))}
                </Stack>
                {shell ? (
                    <InstallSection api={api} language={language}
                                    onChanged={read} onError={onError}
                                    onDone={(text) => showSnackbar(
                                        text, "success")}/>
                ) : null}
                {access === "elsewhere" ? (
                    <Note>{i18n.t("plugins_browser_note")}</Note>
                ) : null}
            </CardFrame>
            {shell && selected ? (
                <PluginDetailView api={api} id={selected} language={language}
                                  onChanged={read} onError={onError}
                                  onRemoved={() => {
                                      setSelected(null);
                                      read();
                                  }}/>
            ) : null}
            {shell ? (
                <DevSection api={api} onChanged={read} onError={onError}/>
            ) : null}
        </GuideFrame>
    );
}

export default function PluginsPage({api = httpPluginsApi}: {
    api?: PluginsApi;
}) {
    return (
        <FeedbackProvider>
            <PluginsContent api={api}/>
        </FeedbackProvider>
    );
}
