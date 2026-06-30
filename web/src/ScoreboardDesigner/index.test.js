import React from "react";
import {Provider} from "react-redux";
import {
    act,
    fireEvent,
    render,
    screen,
    waitFor,
} from "@testing-library/react";
import {tshStore} from "../redux/store";
import {tshStateSlice} from "../redux/tshState";
import {SAMPLE_STATE} from "./bindings";
import ScoreboardDesigner from ".";

jest.mock("../ScoreboardPage/websocketInit", () => ({
    __esModule: true,
    default: jest.fn(),
}));

beforeAll(() => {
    global.ResizeObserver = class {
        observe() {}
        disconnect() {}
    };
    Element.prototype.animate = jest.fn(() => ({
        cancel: jest.fn(),
        finished: Promise.resolve(),
    }));
});

describe("ScoreboardDesigner", () => {
    test("adds a dynamic layer and exposes editable properties", () => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=test-token");
        const startggState = JSON.parse(JSON.stringify(SAMPLE_STATE));
        startggState.score["1"].team["1"].player["1"].name = "STARTGG PLAYER";
        tshStore.dispatch(tshStateSlice.actions.overwrite({
            state: startggState,
            revision: 1,
        }));

        render(
            <Provider store={tshStore}>
                <ScoreboardDesigner />
            </Provider>,
        );

        expect(screen.getByText("Scoreboard Designer")).toBeInTheDocument();
        expect(screen.getByText("PNGを追加")).toBeInTheDocument();
        expect(screen.getByText(/実データ/)).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", {name: "追加"}));

        expect(document.querySelectorAll(".designer-layer-row")).toHaveLength(1);
        expect(screen.getByDisplayValue("STARTGG PLAYER")).toBeInTheDocument();
        expect(screen.getAllByText("STARTGG PLAYER").length).toBeGreaterThan(0);

        const updatedState = JSON.parse(JSON.stringify(startggState));
        updatedState.score["1"].team["1"].player["1"].name = "UPDATED PLAYER";
        act(() => {
            tshStore.dispatch(tshStateSlice.actions.overwrite({
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

        const finalState = JSON.parse(JSON.stringify(updatedState));
        finalState.score["1"].team["1"].player["1"].name = "FINAL PLAYER";
        act(() => {
            tshStore.dispatch(tshStateSlice.actions.overwrite({
                state: finalState,
                revision: 3,
            }));
        });
        expect(screen.getByText("FINAL PLAYER")).toBeInTheDocument();
        expect(screen.getByDisplayValue("Main Player")).toBeInTheDocument();
    });

    test("blocks use outside the desktop Designer window", () => {
        window.history.replaceState({}, "", "/web?view=designer");
        render(
            <Provider store={tshStore}>
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
            <Provider store={tshStore}>
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
});
