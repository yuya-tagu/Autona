// The Companion guide's button icons (ADR-005 S-final SF-4a-2): what the Qt
// window painted with QPainter, drawn on a canvas -- a 144 px square of the
// row's colour, an optional bold top label and the main label shrunk until
// it fits its box. A data: URL, shown as the image and copied as it is
// (clipboard.ts's copyImage reads it as any image URL).

export const ICON_SIZE = 144;

export interface KeyIcon {
    bg: string;
    // The small line above ("P1"), or none.
    top: string | null;
    // The label, "\n" between its lines.
    main: string;
}

const FONT = "Roboto, Helvetica, Arial, sans-serif";
const LINE_HEIGHT = 1.2;
const MAIN_START = 72;
const MAIN_MIN = 12;
const MAIN_STEP = 4;

interface Box {
    x: number;
    y: number;
    w: number;
    h: number;
}

// The main label's size: the window's (72 px down by 4 while its widest
// line or its lines' height overflow the box, not below 12).
function mainSize(context: CanvasRenderingContext2D, lines: string[],
                  box: Box): number {
    let size = MAIN_START;
    while (size > MAIN_MIN) {
        context.font = `bold ${size}px ${FONT}`;
        const width = Math.max(...lines.map(
            (line) => context.measureText(line).width));
        const height = size * LINE_HEIGHT * lines.length;
        if (width <= box.w && height <= box.h) {
            break;
        }
        size -= MAIN_STEP;
    }
    return size;
}

// The icon as a PNG data: URL; null where the page has no 2d canvas.
export function drawKeyIcon(icon: KeyIcon): string | null {
    const canvas = document.createElement("canvas");
    canvas.width = ICON_SIZE;
    canvas.height = ICON_SIZE;
    const context = canvas.getContext("2d");
    if (!context) {
        return null;
    }
    context.fillStyle = icon.bg;
    context.fillRect(0, 0, ICON_SIZE, ICON_SIZE);
    context.fillStyle = "#ffffff";
    context.textAlign = "center";
    context.textBaseline = "middle";

    let box: Box = {x: 10, y: 10, w: ICON_SIZE - 20, h: ICON_SIZE - 20};
    if (icon.top) {
        context.font = `bold 40px ${FONT}`;
        context.fillText(icon.top, ICON_SIZE / 2, 14 + 46 / 2);
        box = {x: 8, y: 58, w: ICON_SIZE - 16, h: ICON_SIZE - 70};
    }

    const lines = icon.main.split("\n");
    const size = mainSize(context, lines, box);
    context.font = `bold ${size}px ${FONT}`;
    const step = size * LINE_HEIGHT;
    const first = box.y + box.h / 2 - (step * (lines.length - 1)) / 2;
    lines.forEach((line, index) => {
        context.fillText(line, box.x + box.w / 2, first + index * step);
    });
    return canvas.toDataURL("image/png");
}
