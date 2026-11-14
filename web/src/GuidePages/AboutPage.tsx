import React from "react";
import {Box, Card, CardContent, Link, Stack, Typography} from "@mui/material";
import i18n from "../i18n/config";
import {FeedbackProvider} from "../ScoreboardPage/Feedback";
import {getWithRetry} from "../PanelShell/requests";
import {Code, CopyButton, GuideFrame} from "./parts";

// About Autona (ADR-005 S-final SF-4a-2), /web/about: the desktop's About
// dialog as a page. The legal and attribution text stays English, as the
// dialog's did (proper nouns and licence names verbatim). Its links open
// in the system browser (the shared view hands a user-initiated http(s)
// window to it); the e-mail address is shown with a copy button instead of
// a mailto: link, which the shared view does not hand on.

// Must be src/Autona.py's BUSINESS_INQUIRY_EMAIL (tests/test_web_panel_host
// .py compares the two).
export const BUSINESS_INQUIRY_EMAIL = "taguyangames@gmail.com";

export const HEALTH_PATH = "/api/v1/health";

interface Envelope<T> {
    ok: boolean;
    data: T;
}

function Outside({href, children}: {href: string; children: React.ReactNode}) {
    return (
        <Link href={href} target="_blank" rel="noopener noreferrer">
            {children}
        </Link>
    );
}

function AboutContent() {
    const [version, setVersion] = React.useState<string | null>(null);

    React.useEffect(() => {
        let live = true;
        getWithRetry<Envelope<{version?: string}>>(HEALTH_PATH, () => live)
            .then((body) => {
                if (live) {
                    setVersion(body.data?.version ?? "?");
                }
            }, (error: unknown) => {
                console.error(error);
                if (live) {
                    setVersion("?");
                }
            });
        return () => {
            live = false;
        };
    }, []);

    return (
        // The title already names the app ("About Autona").
        <GuideFrame panel="about" title={i18n.t("guide_about_title")}
                    documentTitle={i18n.t("guide_about_title")}
                    openInBrowser={false}>
            <Card variant="outlined">
                <CardContent>
                    <Stack spacing={2}>
                        <Typography variant="subtitle1" component="h2"
                                    sx={{fontWeight: "bold"}}>
                            {version === null ? "Autona" : `Autona v${version}`}
                        </Typography>
                        <Typography variant="body2">
                            © 2026 taguyan and Autona contributors. Released
                            under the MIT License.
                        </Typography>
                        <Typography variant="body2">
                            Based on{" "}
                            <Outside href="https://github.com/joaorb64/TournamentStreamHelper">
                                TournamentStreamHelper
                            </Outside>{" "}
                            (MIT License) by João Ribeiro Bezerra.
                        </Typography>
                        <Typography variant="body2">
                            Tournament data powered by{" "}
                            <Outside href="https://www.start.gg/">start.gg</Outside>.
                            Autona is an unofficial tool and is not affiliated
                            with or endorsed by Start.gg, Inc.
                        </Typography>
                        <Typography variant="body2">
                            Built with Qt /{" "}
                            <Outside href="https://pypi.org/project/PySide6/">
                                PySide6
                            </Outside>{" "}
                            under the GNU LGPL v3 (dynamically linked; the
                            libraries in <Code>_internal/</Code> can be
                            replaced by the user).
                        </Typography>
                        <Box>
                            <Typography variant="body2">
                                Setup and operation support for events is
                                available:
                            </Typography>
                            <Stack direction="row" spacing={1}
                                   alignItems="center" sx={{mt: 0.5}}>
                                <Code>{BUSINESS_INQUIRY_EMAIL}</Code>
                                <CopyButton
                                    text={BUSINESS_INQUIRY_EMAIL}
                                    name={i18n.t("guide_about_copy_email")}/>
                            </Stack>
                        </Box>
                        <Typography variant="body2">
                            License details: see <Code>LICENSE</Code>,{" "}
                            <Code>TERMS.md</Code>, <Code>TRADEMARK.md</Code>,{" "}
                            <Code>THIRD-PARTY-NOTICES.md</Code> and the{" "}
                            <Code>licenses/</Code> folder next to the
                            executable.
                        </Typography>
                    </Stack>
                </CardContent>
            </Card>
        </GuideFrame>
    );
}

export default function AboutPage() {
    return (
        <FeedbackProvider>
            <AboutContent/>
        </FeedbackProvider>
    );
}
