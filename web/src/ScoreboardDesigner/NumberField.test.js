import React from "react";
import {
    fireEvent,
    render,
    screen,
} from "@testing-library/react";
import NumberField from "./NumberField";

function ControlledNumberField({initialValue = 42, step = 1}) {
    const [value, setValue] = React.useState(initialValue);
    return (
        <NumberField
            label="X"
            value={value}
            step={step}
            onChange={setValue}
        />
    );
}

describe("NumberField", () => {
    test("allows the current value to be cleared before entering a replacement", () => {
        render(<ControlledNumberField />);
        const input = screen.getByLabelText("X");

        fireEvent.focus(input);
        fireEvent.change(input, {target: {value: ""}});
        expect(input).toHaveValue("");

        fireEvent.change(input, {target: {value: "128"}});
        expect(input).toHaveValue("128");
        fireEvent.blur(input);
        expect(input).toHaveValue("128");
    });

    test("restores the numeric value when an empty draft loses focus", () => {
        render(<ControlledNumberField />);
        const input = screen.getByLabelText("X");

        fireEvent.focus(input);
        fireEvent.change(input, {target: {value: ""}});
        fireEvent.blur(input);

        expect(input).toHaveValue("42");
    });

    test("changes the value by dragging the scrubber horizontally", () => {
        render(<ControlledNumberField initialValue={10} />);
        const scrubber = screen.getByLabelText("Xをドラッグして調整");

        fireEvent(scrubber, new MouseEvent("pointerdown", {
            bubbles: true,
            clientX: 5,
        }));
        fireEvent(window, new MouseEvent("pointermove", {
            bubbles: true,
            clientX: 20,
        }));
        fireEvent(window, new MouseEvent("pointerup", {bubbles: true}));

        expect(screen.getByLabelText("X")).toHaveValue("25");
    });
});
