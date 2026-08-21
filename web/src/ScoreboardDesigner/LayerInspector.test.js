import React from "react";
import {fireEvent, render, screen} from "@testing-library/react";
import LayerInspector from "./LayerInspector";
import {
    createBindingLayer,
    createDefaultGameHistoryImageStyle,
} from "./model";

describe("LayerInspector", () => {
    test("assigns an Autona binding to imported static data-text", () => {
        const layer = createBindingLayer("p1.name", 1);
        delete layer.binding;
        layer.text = "Grand Finals";
        const onChange = jest.fn();

        render(<LayerInspector layer={layer} onChange={onChange} />);

        fireEvent.mouseDown(screen.getByRole("combobox", {
            name: "データ対応",
        }));
        fireEvent.click(screen.getByRole("option", {name: "ラウンド"}));

        expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
            binding: "match.round",
            events: expect.objectContaining({
                dataChange: expect.objectContaining({
                    watchBindings: ["match.round"],
                }),
            }),
        }));
    });

    test("toggles horizontal and vertical reflection", () => {
        const layer = createBindingLayer("p1.name", 1);
        const onChange = jest.fn();

        render(<LayerInspector layer={layer} onChange={onChange} />);

        fireEvent.click(screen.getByRole("button", {name: "左右反転"}));
        fireEvent.click(screen.getByRole("button", {name: "上下反転"}));

        expect(onChange).toHaveBeenNthCalledWith(1, {flipX: true});
        expect(onChange).toHaveBeenNthCalledWith(2, {flipY: true});
    });

    test("shows Game-History settings when the selected layer is an allowed SVG", () => {
        const layer = {
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/vector.svg",
            objectFit: "contain",
        };
        const onChange = jest.fn();
        const {rerender} = render(
            <LayerInspector layer={layer} onChange={onChange} />,
        );

        expect(screen.queryByText("Game-History表示")).toBeNull();
        rerender(
            <LayerInspector
                layer={layer}
                allowGameHistory
                onChange={onChange}
            />,
        );
        expect(screen.getByText("Game-History表示")).toBeInTheDocument();
    });

    test("adds part mode without removing the legacy slot settings", () => {
        const layer = {
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/vector.svg",
            objectFit: "contain",
            gameHistoryStyle: {
                ...createDefaultGameHistoryImageStyle(2),
            },
        };
        const onChange = jest.fn();
        const onPartEditingChange = jest.fn();

        render(
            <LayerInspector
                layer={layer}
                allowGameHistory
                onGameHistoryPartEditingChange={onPartEditingChange}
                onChange={onChange}
            />,
        );

        fireEvent.click(screen.getByRole("switch", {
            name: "SVG内部パーツを個別指定",
        }));

        expect(onPartEditingChange).toHaveBeenCalledWith(true);
        expect(screen.getByLabelText("ゲーム番号")).toHaveValue("3");
    });

    test("offers independent player set lamp assignments", () => {
        const layer = {
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/vector.svg",
            objectFit: "contain",
            gameHistoryStyle: {
                ...createDefaultGameHistoryImageStyle(),
                mode: "parts",
                parts: [],
            },
        };
        const onRoleChange = jest.fn();

        render(
            <LayerInspector
                layer={layer}
                allowGameHistory
                selectedGameHistoryPartPath={[0]}
                onGameHistoryPartRoleChange={onRoleChange}
                onChange={jest.fn()}
            />,
        );

        fireEvent.mouseDown(screen.getByRole("combobox", {name: "点灯条件"}));
        expect(screen.getByRole("option", {name: "P1セット1"})).toBeInTheDocument();
        expect(screen.getByRole("option", {name: "P1セット2"})).toBeInTheDocument();
        expect(screen.getByRole("option", {name: "P2セット1"})).toBeInTheDocument();
        expect(screen.getByRole("option", {name: "P2セット2"})).toBeInTheDocument();
        fireEvent.click(screen.getByRole("option", {name: "P2セット2"}));

        expect(onRoleChange).toHaveBeenCalledWith("p2-set", 1);
    });

    test("shows the six BO5 SVG target controls", () => {
        const layer = {
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/vector.svg",
            objectFit: "contain",
            gameHistoryStyle: {
                ...createDefaultGameHistoryImageStyle(),
                mode: "parts",
                parts: [{path: [], role: "p1-set", setIndex: 0}],
            },
        };
        const onTargetAssign = jest.fn();

        render(
            <LayerInspector
                layer={layer}
                allowGameHistory
                onGameHistoryPartTargetAssign={onTargetAssign}
                onChange={jest.fn()}
            />,
        );

        expect(screen.getByText("P1/P2セット1・2・共有セット3への割り当て")).toBeInTheDocument();
        expect(screen.getByText("このSVGレイヤー")).toBeInTheDocument();
        expect(screen.getByRole("button", {
            name: "P1セット1にこのSVGレイヤーを指定",
        })).toBeInTheDocument();
        expect(screen.getByRole("button", {
            name: "P1セット2にこのSVGレイヤーを指定",
        })).toBeInTheDocument();
        expect(screen.getByRole("button", {
            name: "共通セット3（P1）にこのSVGレイヤーを指定",
        })).toBeInTheDocument();
        expect(screen.getByRole("button", {
            name: "P2セット1にこのSVGレイヤーを指定",
        })).toBeInTheDocument();
        expect(screen.getByRole("button", {
            name: "P2セット2にこのSVGレイヤーを指定",
        })).toBeInTheDocument();
        expect(screen.getByRole("button", {
            name: "共通セット3（P2）にこのSVGレイヤーを指定",
        })).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", {
            name: "P1セット1にこのSVGレイヤーを指定",
        }));
        expect(onTargetAssign).toHaveBeenCalledWith({
            role: "p1-set",
            setIndex: 0,
        });
    });

    test("keeps all SVG target assignments visible independently of the selected layer", () => {
        const layer = {
            ...createBindingLayer("p1.name", 1),
            type: "image",
            asset: "assets/vector.svg",
            objectFit: "contain",
        };

        render(
            <LayerInspector
                layer={layer}
                gameHistoryAssignments={[
                    {
                        target: {role: "p1-set", setIndex: 2},
                        layerId: "lamp-3",
                        layerName: "P1 3点目ランプ",
                    },
                ]}
                onChange={jest.fn()}
            />,
        );

        expect(screen.getByText("Game-History SVG設定状況")).toBeInTheDocument();
        expect(screen.getByText("P1 3点目ランプ")).toBeInTheDocument();
        expect(screen.getByText("共通セット3（P2）")).toBeInTheDocument();
        expect(screen.getAllByText("未指定").length).toBeGreaterThan(0);
    });
});
