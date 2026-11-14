import React from "react";
import {
    Box,
    Button,
    Card,
    CardContent,
    IconButton,
    Stack,
    Tooltip,
    Typography,
} from "@mui/material";
import ContentCopyOutlined from "@mui/icons-material/ContentCopyOutlined";
import i18n from "../i18n/config";
import {usePanelShell} from "../PanelShell/context";
import {copyText} from "../PanelShell/clipboard";
import {showSnackbar} from "../ScoreboardPage/Feedback";
import OpenInBrowserButton from "./OpenInBrowserButton";

// What the guide pages (ADR-005 S-final SF-4a-2) share: the page's frame,
// its cards, the numbered steps and the copy buttons. Text only: nothing
// here renders HTML (the shared view never shows HTML it did not make).

export function GuideFrame({panel, title, documentTitle, description,
                            openInBrowser = true, children}: {
    panel: string;
    title: string;
    // The tab's title; "Autona <title>" unless the title names Autona
    // already (About).
    documentTitle?: string;
    description?: string;
    // The "open in the default browser" button (the API, Companion and OBS
    // guides; not About).
    openInBrowser?: boolean;
    children: React.ReactNode;
}) {
    const {language} = usePanelShell();

    React.useEffect(() => {
        const previous = document.title;
        document.title = documentTitle ?? `Autona ${title}`;
        return () => {
            document.title = previous;
        };
    }, [title, documentTitle, language]);

    return (
        <Box component="main" sx={{p: 2, bgcolor: "background.default",
                                   minHeight: "100vh"}}>
            <Stack spacing={2} sx={{maxWidth: 1000, mx: "auto"}}>
                <Stack direction="row" alignItems="center" spacing={1}
                       flexWrap="wrap" useFlexGap>
                    <Typography variant="h6" component="h1" sx={{flex: 1}}>
                        {title}
                    </Typography>
                    {openInBrowser ? <OpenInBrowserButton panel={panel}/> : null}
                </Stack>
                {description ? (
                    <Typography variant="body2">{description}</Typography>
                ) : null}
                {children}
            </Stack>
        </Box>
    );
}

export function CardFrame({title, children}: {
    title: string;
    children: React.ReactNode;
}) {
    return (
        <Card variant="outlined" component="section" aria-label={title}>
            <CardContent>
                <Stack spacing={1.5}>
                    <Typography variant="subtitle1" component="h2">
                        {title}
                    </Typography>
                    {children}
                </Stack>
            </CardContent>
        </Card>
    );
}

export function Note({children}: {children: React.ReactNode}) {
    return (
        <Typography variant="body2" color="text.secondary">
            {children}
        </Typography>
    );
}

// `text` with a line break allowed after each "/" and "\\" (an URL, a
// path), so a narrow column breaks it between its parts, not inside one.
export function withBreaks(text: string): React.ReactNode[] {
    const nodes: React.ReactNode[] = [];
    let part = "";
    for (const character of text) {
        part += character;
        if (character === "/" || character === "\\") {
            nodes.push(part, <wbr key={nodes.length}/>);
            part = "";
        }
    }
    if (part) {
        nodes.push(part);
    }
    return nodes;
}

// A value to copy as it is: an URL, a body, a path. Broken between its
// parts (withBreaks); inside one only if that part alone is wider than
// the line.
export function Code({children}: {children: React.ReactNode}) {
    return (
        <Typography component="code" variant="body2"
                    sx={{fontFamily: "monospace", overflowWrap: "break-word"}}>
            {typeof children === "string" ? withBreaks(children) : children}
        </Typography>
    );
}

// Whether an element (a table's container) is narrower than `width` CSS
// px, followed as it resizes: the app's view is ~600 CSS px wide (a card's
// table gets ~530). Wide until measured; a hidden tab (0 wide) keeps the
// last answer.
export function useNarrow<T extends HTMLElement>(
    width: number): [(element: T | null) => void, boolean] {
    const [element, setElement] = React.useState<T | null>(null);
    const [narrow, setNarrow] = React.useState(false);
    React.useLayoutEffect(() => {
        if (!element) {
            return undefined;
        }
        const measure = () => {
            if (element.clientWidth > 0) {
                setNarrow(element.clientWidth < width);
            }
        };
        measure();
        if (typeof ResizeObserver === "undefined") {
            return undefined;
        }
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [element, width]);
    return [setElement, narrow];
}

export function Steps({steps}: {steps: string[]}) {
    return (
        <Box component="ol" sx={{m: 0, pl: 3}}>
            {steps.map((step) => (
                <Typography component="li" variant="body2" key={step}
                            sx={{mb: 0.5}}>
                    {step}
                </Typography>
            ))}
        </Box>
    );
}

export async function copyAndTell(text: string): Promise<void> {
    if (await copyText(text)) {
        showSnackbar(i18n.t("guide_copied"), "success");
    } else {
        showSnackbar(i18n.t("guide_copy_failed"), "error");
    }
}

// A copy button; `name` is what it copies, for a screen reader and the
// tests ("Copy URL: Player 1 score +1").
// The same as an icon, for a table's narrow column: `name` is its tooltip
// and what a screen reader says.
export function CopyIconButton({text, name}: {text: string; name: string}) {
    return (
        <Tooltip title={name}>
            <span>
                <IconButton size="small" aria-label={name} disabled={!text}
                            onClick={() => void copyAndTell(text)}>
                    <ContentCopyOutlined fontSize="small"/>
                </IconButton>
            </span>
        </Tooltip>
    );
}

export function CopyButton({text, label, name, disabled}: {
    text: string;
    label?: string;
    name?: string;
    disabled?: boolean;
}) {
    return (
        <Button size="small" variant="outlined" disabled={disabled || !text}
                aria-label={name}
                onClick={() => void copyAndTell(text)}>
            {label ?? i18n.t("guide_copy")}
        </Button>
    );
}
