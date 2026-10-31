// jest-dom adds custom jest matchers for asserting on DOM nodes.
// allows you to do things like:
// expect(element).toHaveTextContent(/react/i)
// learn more: https://github.com/testing-library/jest-dom
import '@testing-library/jest-dom';

// react-router v7 reads TextEncoder when it is imported; jest 27's jsdom
// has none (the PanelShell tests import the router).
import {TextDecoder, TextEncoder} from "util";

if (typeof global.TextEncoder === "undefined") {
    global.TextEncoder = TextEncoder;
    global.TextDecoder = TextDecoder;
}
