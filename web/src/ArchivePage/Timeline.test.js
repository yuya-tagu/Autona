import React from "react";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import Timeline from "./Timeline";

// The timeline's IN / OUT handles (ADR-006 A4): a drag moves the edge with
// the pointer and ends on the button's release; one whose end the handle did
// not hear (the button let go elsewhere, the pointer lost, another set
// selected) ends without moving anything more.

// jsdom has no PointerEvent: a MouseEvent with a pointer id (clientX and
// buttons are the MouseEvent's).
class FakePointerEvent extends MouseEvent {
    constructor(type, init = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
    }
}

const SETS = {duration: 1000, sets: [
    {no: 1, start: 100, end: 300, matched: true},
    {no: 2, start: 400, end: 600, matched: true},
]};

let box;
beforeEach(() => {
    window.PointerEvent = FakePointerEvent;
    // Every lane 1000 px wide from x 0: a pixel is a second.
    box = jest.spyOn(Element.prototype, "getBoundingClientRect")
        .mockReturnValue({left: 0, top: 0, width: 1000, height: 30, right: 1000,
                          bottom: 30, x: 0, y: 0});
});

afterEach(() => {
    cleanup();
    box.mockRestore();
    delete window.PointerEvent;
});

function draw(onEdge, selected = SETS.sets[0]) {
    return (
        <Timeline sets={SETS} rows={SETS.sets} selected={selected} view="all"
                  onView={() => undefined} time={0} onSeek={() => undefined}
                  onPick={() => undefined} onEdge={onEdge}
                  range={{start: 0, end: 1000}}/>
    );
}

const handle = () => screen.getByTestId("archive-handle-in");
const grab = () => fireEvent.pointerDown(handle(), {button: 0, buttons: 1,
                                                    clientX: 100});
const move = (clientX, buttons = 1) =>
    fireEvent.pointerMove(handle(), {clientX, buttons});

test("a drag moves the edge with the pointer and ends on the release", () => {
    const onEdge = jest.fn();
    render(draw(onEdge));
    grab();
    move(150);
    fireEvent.pointerUp(handle(), {clientX: 160, buttons: 0});
    expect(onEdge.mock.calls).toEqual([["start", 150], ["start", 160]]);
    // Over: a move is no drag.
    move(170);
    expect(onEdge).toHaveBeenCalledTimes(2);
});

test("a move without the button ends the drag where it is", () => {
    const onEdge = jest.fn();
    render(draw(onEdge));
    grab();
    move(150, 0);
    move(170);
    expect(onEdge).not.toHaveBeenCalled();
});

test("a handle that lost the pointer ends the drag", () => {
    const onEdge = jest.fn();
    render(draw(onEdge));
    grab();
    fireEvent.lostPointerCapture(handle());
    move(150);
    fireEvent.pointerUp(handle(), {clientX: 160, buttons: 0});
    expect(onEdge).not.toHaveBeenCalled();
});

test("another set selected ends the drag", () => {
    const onEdge = jest.fn();
    const {rerender} = render(draw(onEdge));
    grab();
    rerender(draw(onEdge, SETS.sets[1]));
    move(450);
    expect(onEdge).not.toHaveBeenCalled();
});
