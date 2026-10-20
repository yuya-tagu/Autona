import React from "react";
import {Provider} from "react-redux";
import {
    act,
    fireEvent,
    render,
    screen,
    waitFor,
} from "@testing-library/react";
import {autonaStore} from "../redux/store";
import {autonaStateSlice} from "../redux/autonaState";
import {SAMPLE_STATE} from "./bindings";
import ScoreboardDesigner from ".";

const mockSocketHandlers = {};

jest.mock("../ScoreboardPage/websocketInit", () => ({
    __esModule: true,
    default: jest.fn(),
}));

jest.mock("../websocketConnection", () => ({
    __esModule: true,
    default: {
        instance: () => ({
            on: jest.fn((event, handler) => {
                mockSocketHandlers[event] = handler;
            }),
            off: jest.fn((event) => {
                delete mockSocketHandlers[event];
            }),
            emit: jest.fn(),
        }),
    },
}));

beforeAll(() => {
    global.ResizeObserver = class {
        observe() {}
        disconnect() {}
    };
    URL.createObjectURL = jest.fn(() => "blob:preview");
    URL.revokeObjectURL = jest.fn();
    Element.prototype.animate = jest.fn(() => ({
        cancel: jest.fn(),
        finished: Promise.resolve(),
    }));
});

describe("ScoreboardDesigner", () => {
    beforeEach(() => {
        Object.keys(mockSocketHandlers).forEach((key) => {
            delete mockSocketHandlers[key];
        });
    });

    test("opens asset imports from the toolbar Import menu", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");

        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "Import"}));

        expect(screen.getByRole("menuitem", {name: "PNGを追加"}))
            .toBeInTheDocument();
        expect(screen.getByRole("menuitem", {name: "SVGを追加"}))
            .toBeInTheDocument();
        expect(screen.queryByRole("menuitem", {
            name: "制作ファイル交換ZIP",
        })).not.toBeInTheDocument();
    });

    test("zooms only through the canvas viewport and can return to 100%", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");

        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        const viewport = document.querySelector(".designer-canvas-viewport");
        fireEvent.click(screen.getByRole("button", {
            name: "キャンバスを100%に戻す",
        }));
        expect(screen.getByRole("button", {
            name: "キャンバスを100%に戻す",
        })).toHaveTextContent("100%");

        const allowed = fireEvent.wheel(viewport, {
            ctrlKey: true,
            deltaY: -100,
        });
        expect(allowed).toBe(false);
        expect(screen.getByRole("button", {
            name: "キャンバスを100%に戻す",
        })).toHaveTextContent("110%");

        fireEvent.click(screen.getByRole("button", {
            name: "キャンバスを100%に戻す",
        }));
        expect(screen.getByRole("button", {
            name: "キャンバスを100%に戻す",
        })).toHaveTextContent("100%");
    });

    test("adds a dynamic layer and exposes editable properties", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        const startggState = JSON.parse(JSON.stringify(SAMPLE_STATE));
        startggState.score["1"].team["1"].player["1"].name = "STARTGG PLAYER";
        autonaStore.dispatch(autonaStateSlice.actions.overwrite({
            state: startggState,
            revision: 1,
        }));

        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        expect(screen.getByText("Scoreboard Designer")).toBeInTheDocument();
        expect(screen.getByRole("button", {name: "Import"})).toBeInTheDocument();
        expect(screen.getByLabelText("デザイン名").closest("aside"))
            .toHaveClass("designer-left-sidebar");
        expect(screen.queryByRole("button", {name: "閉じる"}))
            .not.toBeInTheDocument();
        const liveDataToggle = screen.getByRole("switch", {name: "実データを使用"});
        expect(liveDataToggle).not.toBeChecked();
        expect(screen.getByText("サンプル")).toBeInTheDocument();
        expect(screen.getByRole("button", {name: "Design"})).toBeInTheDocument();
        expect(screen.getByRole("button", {name: "Animate"})).toBeInTheDocument();

        fireEvent.click(liveDataToggle);
        fireEvent.click(screen.getByRole("button", {name: "追加"}));

        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(1);
        expect(screen.getByDisplayValue("STARTGG PLAYER")).toBeInTheDocument();
        expect(screen.getAllByText("STARTGG PLAYER").length).toBeGreaterThan(0);

        const updatedState = JSON.parse(JSON.stringify(startggState));
        updatedState.score["1"].team["1"].player["1"].name = "UPDATED PLAYER";
        act(() => {
            autonaStore.dispatch(autonaStateSlice.actions.overwrite({
                state: updatedState,
                revision: 2,
            }));
        });
        expect(screen.getAllByText("UPDATED PLAYER").length).toBeGreaterThan(0);
        expect(screen.getByDisplayValue("UPDATED PLAYER")).toBeInTheDocument();

        fireEvent.change(screen.getByLabelText("レイヤー名"), {
            target: {value: "Main Player"},
        });
        expect(screen.getByDisplayValue("Main Player")).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", {name: "Animate"}));
        expect(screen.getByRole("combobox", {name: "アニメーショントラック"}))
            .toHaveTextContent("シーン表示時");
        expect(screen.getByLabelText("ベジエグラフ")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: "Design"}));

        const finalState = JSON.parse(JSON.stringify(updatedState));
        finalState.score["1"].team["1"].player["1"].name = "FINAL PLAYER";
        act(() => {
            autonaStore.dispatch(autonaStateSlice.actions.overwrite({
                state: finalState,
                revision: 3,
            }));
        });
        expect(screen.getByText("FINAL PLAYER")).toBeInTheDocument();
        expect(screen.getByDisplayValue("Main Player")).toBeInTheDocument();
    });

    test("deletes the selected canvas layer with the Delete key", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(1);

        fireEvent.keyDown(window, {key: "Delete"});

        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(0);
        expect(screen.getByText("PNGまたは動的要素を追加してください。")).toBeInTheDocument();
    });

    test("selects all canvas layers with Ctrl+A", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));

        fireEvent.keyDown(window, {key: "a", ctrlKey: true});

        expect(document.querySelectorAll(".designer-layer-row.selected")).toHaveLength(2);
    });

    test("loads the selected built-in scoreboard template", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        const templateSelect = screen.getByRole("combobox", {name: "標準テンプレート"});
        expect(templateSelect)
            .toHaveTextContent("default_board_universal");

        fireEvent.mouseDown(templateSelect);
        fireEvent.click(screen.getByRole("option", {name: "default_board_simple"}));
        fireEvent.click(screen.getByRole("button", {name: "読込"}));

        expect(screen.getByDisplayValue("default_board_simple"))
            .toBeInTheDocument();
        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(9);
    });

    test("shows the save button as save only", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        expect(screen.getByRole("button", {name: "保存"})).toBeInTheDocument();
        expect(screen.queryByRole("button", {name: "保存・OBS出力"})).not.toBeInTheDocument();
    });

    test("collapses and expands grouped layers in the layer list", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        const rows = document.querySelectorAll(".designer-layer-row");
        fireEvent.click(rows[1]);
        fireEvent.click(rows[0], {ctrlKey: true});
        fireEvent.click(screen.getByRole("button", {name: "グループ化"}));

        expect(screen.getByText("グループ 1")).toBeInTheDocument();
        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(2);

        fireEvent.click(screen.getByRole("button", {name: "グループ 1を折りたたみ"}));
        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(0);

        fireEvent.click(screen.getByRole("button", {name: "グループ 1を展開"}));
        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(2);
    });

    test("toggles grouped layers visibility without showing a count", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        const rows = document.querySelectorAll(".designer-layer-row");
        fireEvent.click(rows[1]);
        fireEvent.click(rows[0], {ctrlKey: true});
        fireEvent.click(screen.getByRole("button", {name: "グループ化"}));

        const groupRow = document.querySelector(".designer-layer-group-row");
        expect(groupRow).not.toHaveTextContent("2");
        expect(document.querySelectorAll(".designer-canvas-layer")).toHaveLength(2);

        fireEvent.click(screen.getByRole("button", {name: "グループ 1を非表示"}));
        expect(document.querySelectorAll(".designer-canvas-layer")).toHaveLength(0);

        fireEvent.click(screen.getByRole("button", {name: "グループ 1を表示"}));
        expect(document.querySelectorAll(".designer-canvas-layer")).toHaveLength(2);
    });

    test("alt-click solos a layer visibility toggle", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        expect(document.querySelectorAll(".designer-canvas-layer")).toHaveLength(2);

        const visibilityButtons = screen.getAllByRole("button", {name: /を非表示/});
        fireEvent.click(visibilityButtons[0], {altKey: true});

        expect(document.querySelectorAll(".designer-canvas-layer")).toHaveLength(1);
    });

    test("renames a grouped layer folder", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        const rows = document.querySelectorAll(".designer-layer-row");
        fireEvent.click(rows[1]);
        fireEvent.click(rows[0], {ctrlKey: true});
        fireEvent.click(screen.getByRole("button", {name: "グループ化"}));

        fireEvent.doubleClick(screen.getByText("グループ 1"));
        fireEvent.change(screen.getByDisplayValue("グループ 1"), {
            target: {value: "選手名グループ"},
        });
        fireEvent.keyDown(screen.getByDisplayValue("選手名グループ"), {key: "Enter"});

        expect(screen.getByText("選手名グループ")).toBeInTheDocument();
    });

    test("moves a selected group above adjacent layers", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        let rows = document.querySelectorAll(".designer-layer-row");
        fireEvent.click(rows[1]);
        fireEvent.click(rows[0], {ctrlKey: true});
        fireEvent.click(screen.getByRole("button", {name: "グループ化"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));

        const layerList = document.querySelector(".designer-layer-list");
        expect(layerList?.children[0]).toHaveClass("designer-layer-row");

        fireEvent.click(screen.getByText("グループ 1"));
        fireEvent.click(screen.getByRole("button", {name: "前面へ"}));

        expect(layerList?.children[0]).toHaveClass("designer-layer-group-row");
    });

    test("drags a grouped layer folder above adjacent layers", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));
        let rows = document.querySelectorAll(".designer-layer-row");
        fireEvent.click(rows[1]);
        fireEvent.click(rows[0], {ctrlKey: true});
        fireEvent.click(screen.getByRole("button", {name: "グループ化"}));
        fireEvent.click(screen.getByRole("button", {name: "追加"}));

        const layerList = document.querySelector(".designer-layer-list");
        expect(layerList?.children[0]).toHaveClass("designer-layer-row");

        const handle = screen.getByRole("button", {name: "グループ 1を並べ替え"});
        fireEvent.pointerDown(handle, {clientY: 10});
        fireEvent.pointerMove(window, {clientY: -20});
        fireEvent.pointerUp(window);

        expect(layerList?.children[0]).toHaveClass("designer-layer-group-row");
    });

    test("blocks use outside the desktop Designer window", () => {
        window.history.replaceState({}, "", "/web?view=designer");
        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );
        expect(screen.getByText(
            /AutonaデスクトップアプリのBOARDタブ/
        )).toBeInTheDocument();
        expect(screen.queryByText("PNGを追加")).not.toBeInTheDocument();
    });

    test("deletes a saved custom design after confirmation", async () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        global.fetch = jest.fn(async (url, init) => {
            if (url === "/api/designer/projects/finals" &&
                    init?.method === "DELETE") {
                return {
                    ok: true,
                    json: async () => ({slug: "finals"}),
                };
            }
            return {
                ok: true,
                json: async () => ({
                    projects: [{slug: "finals", name: "Finals"}],
                }),
            };
        });
        window.confirm = jest.fn(() => true);

        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );
        fireEvent.click(screen.getByRole("button", {name: /開く/}));
        fireEvent.click(await screen.findByRole(
            "button", {name: "Finalsを削除"}));

        expect(window.confirm).toHaveBeenCalledWith(
            expect.stringContaining("この操作は取り消せません。"));
        await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
            "/api/designer/projects/finals",
            expect.objectContaining({method: "DELETE"}),
        ));
        expect(await screen.findByText(
            "保存済みのデザインはありません。"
        )).toBeInTheDocument();
    });

    test("loads the latest Figma plugin import when Autona notifies the designer", async () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        const pngData = "data:image/png;base64,iVBORw0KGgp0ZXN0";
        global.fetch = jest.fn(async (url) => {
            if (url === pngData) {
                return {blob: async () => new Blob(["png"], {type: "image/png"})};
            }
            return {
                ok: true,
                json: async () => ({
                    project: {
                        schemaVersion: 1,
                        name: "Figma Import",
                        canvas: {
                            width: 1920,
                            height: 1080,
                            backgroundColor: "transparent",
                        },
                        layers: [{
                            id: "frame",
                            name: "Frame",
                            type: "image",
                            asset: "assets/frame.png",
                            objectFit: "contain",
                            x: 0,
                            y: 0,
                            width: 1920,
                            height: 1080,
                            rotation: 0,
                            opacity: 1,
                            zIndex: 1,
                            visible: true,
                            animation: {preset: "none", durationMs: 0, delayMs: 0},
                        }],
                    },
                    assets: {"assets/frame.png": pngData},
                }),
            };
        });

        render(
            <Provider store={autonaStore}>
                <ScoreboardDesigner />
            </Provider>,
        );
        expect(screen.queryByRole("button", {name: "Figmaから受信"})).not.toBeInTheDocument();
        await act(async () => {
            await mockSocketHandlers.designer_import_received();
        });

        expect(await screen.findByDisplayValue("Figma Import")).toBeInTheDocument();
        expect(await screen.findByText("Frame")).toBeInTheDocument();
        await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
            "/api/designer/imports/latest",
            expect.objectContaining({
                headers: expect.objectContaining({
                    "X-Autona-Designer-Token": "test-token",
                }),
            }),
        ));
    });
});
