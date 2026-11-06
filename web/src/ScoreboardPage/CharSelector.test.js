import React from "react";
import {Provider} from "react-redux";
import {configureStore} from "@reduxjs/toolkit";
import {fireEvent, render, screen} from "@testing-library/react";
import {autonaCharactersSlice} from "../redux/autonaState";
import {CharSelector} from "./CharSelector";

// The field shared by the scoreboard and the Player List's editor. The editor
// holds the name field's text; on the scoreboard the field holds it, as it
// always did.

const SKIN = {assets: {"base_files/icon": {asset: "./assets/icon.png"}}};
const MARIO = {en_name: "Mario", display_name: "Mario", skins: [SKIN]};
const LUIGI = {en_name: "Luigi", display_name: "Luigi", skins: [SKIN]};

function renderSelector(props) {
    const store = configureStore({
        reducer: {autonaCharacters: autonaCharactersSlice.reducer},
    });
    store.dispatch(autonaCharactersSlice.actions.overwrite({
        "Mario / Mario": MARIO, "Luigi / Luigi": LUIGI}));
    const onCharNameChanged = jest.fn();
    render(
        <Provider store={store}>
            <CharSelector id="c" charName="" charSkin="" stackProps={{}}
                          onCharNameChanged={onCharNameChanged}
                          onCharSkinChanged={() => undefined} {...props}/>
        </Provider>,
    );
    return onCharNameChanged;
}

function typeAndLeave(text) {
    const input = screen.getAllByRole("combobox")[0];
    fireEvent.focus(input);
    fireEvent.change(input, {target: {value: text}});
    fireEvent.blur(input);
}

test("left alone, a name typed and left stays in the field", () => {
    const changed = renderSelector();

    typeAndLeave("Luigi");

    expect(changed).not.toHaveBeenCalled();
    expect(screen.getAllByRole("combobox")[0]).toHaveValue("Luigi");
});

test("given its text, it shows that one and tells what is typed", () => {
    const told = jest.fn();
    renderSelector({nameText: "Luig", onNameText: told});
    const input = screen.getAllByRole("combobox")[0];
    expect(input).toHaveValue("Luig");

    fireEvent.change(input, {target: {value: "Luigi"}});

    expect(told).toHaveBeenCalledWith("Luigi", "input");
    expect(input).toHaveValue("Luig");
});

test("given its text, it says when it is left, and takes no name by itself", () => {
    const left = jest.fn();
    const changed = renderSelector({nameText: "Luig", onNameText: () => undefined,
                                    onNameLeft: left});

    const input = screen.getAllByRole("combobox")[0];
    fireEvent.focus(input);
    fireEvent.blur(input);

    expect(left).toHaveBeenCalledTimes(1);
    expect(changed).not.toHaveBeenCalled();
});

test("leaving does not pick the row the mouse crossed", async () => {
    const changed = renderSelector({nameText: "", onNameText: () => undefined,
                                    onNameLeft: () => undefined});

    const input = screen.getAllByRole("combobox")[0];
    fireEvent.focus(input);
    fireEvent.mouseDown(input);
    fireEvent.mouseMove(await screen.findByRole("option", {name: /Luigi/}));
    fireEvent.blur(input);

    expect(changed).not.toHaveBeenCalled();
});

test("the closed skin field keeps 56px unless compactSkin is given", () => {
    renderSelector({charName: "Mario", charSkin: "0"});
    const img = screen.getByAltText("Skin 0");
    expect(img.parentElement).toHaveStyle({height: "56px", width: "56px"});
});

test("compactSkin shrinks the shown skin to fit the field", () => {
    renderSelector({charName: "Mario", charSkin: "0", compactSkin: true});
    expect(screen.getByTestId("skin-value")).toHaveStyle({height: "32px", width: "32px"});
});

test("nameInvalid shows the name field in error", () => {
    renderSelector({nameInvalid: true});

    expect(screen.getAllByRole("combobox")[0])
        .toHaveAttribute("aria-invalid", "true");
});
