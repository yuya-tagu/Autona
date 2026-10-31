import React from "react";
import {act, render, screen} from "@testing-library/react";
import {Provider} from "react-redux";
import {BrowserRouter, MemoryRouter, Route, Routes} from "react-router-dom";
import i18n from "../i18n/config";
import {autonaStore} from "../redux/store";
import PanelShell from "./index";
import {BrowserOnly, usePanelShell} from "./context";
import {installPanelShellApi, navigateToPanel, requestFullState} from "./hostApi";
import socketConnection from "../websocketConnection";

// The shell every React panel runs in (ADR-005 S4a): `desktop=1` and `lng`
// from the URL, and the router the desktop's shared view drives.

jest.mock("../ScoreboardPage/websocketInit", () => jest.fn());

jest.mock("../websocketConnection", () => {
    const socket = {connected: false, emit: jest.fn()};
    return {__esModule: true, default: {instance: () => socket}};
});

// The real registry plus two probe panels. No JSX in a jest.mock factory:
// it may not reach the module's own imports (the JSX runtime among them).
jest.mock("./registry", () => {
    const h = jest.requireActual("react").createElement;
    const actual = jest.requireActual("./registry");
    const {usePanelShell: useShell, BrowserOnly: OnlyInBrowser} =
        jest.requireActual("./context");
    const probe = (name) => function Probe() {
        const {panel, desktop} = useShell();
        return h("div", null,
            h("p", null, `probe ${name} (${panel}) desktop=${desktop}`),
            h(OnlyInBrowser, null, h("p", null, "browser part")));
    };
    const PANELS = {
        ...actual.PANELS,
        first: probe("first"),
        second: probe("second"),
    };
    return {
        PANELS,
        panelPage: (panel) => (
            Object.prototype.hasOwnProperty.call(PANELS, panel)
                ? PANELS[panel] : undefined),
    };
});

function renderAt(url) {
    return render(
        <Provider store={autonaStore}>
            <MemoryRouter initialEntries={[url]}>
                <Routes>
                    <Route path="/web/:panel" element={<PanelShell/>}/>
                </Routes>
            </MemoryRouter>
        </Provider>,
    );
}

afterEach(async () => {
    await act(async () => {
        await i18n.changeLanguage("en");
    });
});

test("the panel named in the URL is shown", () => {
    renderAt("/web/first");
    expect(screen.getByText("probe first (first) desktop=false")).toBeInTheDocument();
});

test("browser-only parts are hidden in the desktop's view", () => {
    renderAt("/web/first?desktop=1");
    expect(screen.getByText("probe first (first) desktop=true")).toBeInTheDocument();
    expect(screen.queryByText("browser part")).not.toBeInTheDocument();
});

test("browser-only parts are shown in a browser", () => {
    renderAt("/web/first");
    expect(screen.getByText("browser part")).toBeInTheDocument();
});

test("lng from the URL is the page's language", async () => {
    renderAt("/web/shell-check?desktop=1&lng=ja");

    expect(await screen.findByRole("heading", {name: "パネルシェルの確認"}))
        .toBeInTheDocument();
    expect(screen.getByText("表示先: デスクトップアプリ")).toBeInTheDocument();
    expect(i18n.language).toBe("ja");
    expect(document.documentElement.lang).toBe("ja");
    expect(screen.queryByText("ブラウザでだけ表示")).not.toBeInTheDocument();
});

test("the shell check page in a browser", () => {
    renderAt("/web/shell-check?lng=en");
    expect(screen.getByRole("heading", {name: "Panel shell check"})).toBeInTheDocument();
    expect(screen.getByText("Opened in: a browser")).toBeInTheDocument();
    expect(screen.getByText("Shown in a browser only")).toBeInTheDocument();
});

test("the shell check page puts the tab's title back when it goes away", () => {
    document.title = "Autona";
    const {unmount} = renderAt("/web/shell-check?lng=en");
    expect(document.title).toBe("Autona Panel shell check");

    unmount();

    expect(document.title).toBe("Autona");
});

test("an unknown panel says so", () => {
    renderAt("/web/constructor");
    expect(screen.getByText("Unknown panel: constructor")).toBeInTheDocument();
});

test("the shell starts the Socket.IO state", () => {
    const websocketInit = require("../ScoreboardPage/websocketInit");
    websocketInit.mockClear();
    renderAt("/web/first");
    expect(websocketInit).toHaveBeenCalled();
});

// ── What the desktop's view calls (hostApi) ─────────────────────────────

function renderInBrowserRouter() {
    return render(
        <Provider store={autonaStore}>
            <BrowserRouter>
                <Routes>
                    <Route path="/web/:panel" element={<PanelShell/>}/>
                </Routes>
            </BrowserRouter>
        </Provider>,
    );
}

test("navigate moves the router to another panel, keeping the query", () => {
    window.history.replaceState(null, "", "/web/first?desktop=1&lng=en");
    installPanelShellApi();
    renderInBrowserRouter();
    expect(screen.getByText("probe first (first) desktop=true")).toBeInTheDocument();
    const entries = window.history.length;

    act(() => {
        window.autonaPanelShell.navigate("second");
    });

    expect(screen.getByText("probe second (second) desktop=true")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/web/second");
    expect(window.location.search).toBe("?desktop=1&lng=en");
    // Replaced, not pushed.
    expect(window.history.length).toBe(entries);
});

test("navigate before the shell is mounted moves the URL it starts on", () => {
    window.history.replaceState(null, "", "/web/first?desktop=1");

    navigateToPanel("second");
    renderInBrowserRouter();

    expect(window.location.pathname).toBe("/web/second");
    expect(screen.getByText("probe second (second) desktop=true")).toBeInTheDocument();
});

test("navigate to the panel already shown does nothing", () => {
    window.history.replaceState(null, "", "/web/first?desktop=1");
    renderInBrowserRouter();
    const spy = jest.spyOn(window.history, "replaceState");

    act(() => {
        navigateToPanel("first");
    });

    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
});

test("refresh asks the server for the full state when connected", () => {
    const socket = socketConnection.instance();
    socket.emit.mockClear();

    socket.connected = false;
    requestFullState();
    expect(socket.emit).not.toHaveBeenCalled();

    socket.connected = true;
    requestFullState();
    expect(socket.emit).toHaveBeenCalledWith("program_state", {});
    socket.connected = false;
});

test("usePanelShell outside a shell is a browser page", () => {
    function Probe() {
        const {desktop} = usePanelShell();
        return <p>{`desktop=${desktop}`}</p>;
    }
    render(<><Probe/><BrowserOnly><p>shown</p></BrowserOnly></>);
    expect(screen.getByText("desktop=false")).toBeInTheDocument();
    expect(screen.getByText("shown")).toBeInTheDocument();
});
