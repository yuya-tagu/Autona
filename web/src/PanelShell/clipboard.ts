import {withDeadline} from "./requests";

// The React panels' clipboard (the Commentary page's OBS URL, S4b; the
// Stream page's title and thumbnail, S4e).

export async function copyText(text: string): Promise<boolean> {
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch {
        // Not allowed here (a page from another computer over http): the
        // copy command below still is, on a click.
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try {
        copied = document.execCommand("copy");
    } catch {
        copied = false;
    }
    document.body.removeChild(area);
    return copied;
}

// How long an image copy may take: a page refused the clipboard may never
// hear back from it (a QtWebEngine view that answers no permission request;
// the desktop's shared view grants it to its own origin, ADR-005 S-final
// SF-8b).
export const IMAGE_COPY_TIMEOUT_MS = 10000;

// Whether this page can put an image on the clipboard: the async clipboard
// with ClipboardItem, which browsers give secure contexts only (this PC's
// localhost / 127.0.0.1 -- the desktop's view among them -- or https). A
// page from another computer over http cannot (there is no copy command
// for images either); it offers the image to open or save instead.
export function canCopyImage(): boolean {
    return Boolean(window.isSecureContext
        && typeof ClipboardItem !== "undefined"
        && navigator.clipboard?.write);
}

// The image at `url` as a PNG -- the one image type every clipboard takes
// (the stream thumbnail is a JPEG).
async function pngOf(url: string): Promise<Blob> {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) {
        throw new Error(`GET ${url} failed: HTTP ${response.status}`);
    }
    const bitmap = await createImageBitmap(await response.blob());
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context) {
        throw new Error("no 2d canvas");
    }
    context.drawImage(bitmap, 0, 0);
    bitmap.close?.();
    return new Promise<Blob>((resolve, reject) => canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("no PNG"))),
        "image/png"));
}

// Put the image at `url` on the clipboard (canCopyImage() first). Called
// from the click: the clipboard is asked at once with the PNG still to
// come, so converting it does not outlast the click's permission.
export function copyImage(url: string): Promise<void> {
    return withDeadline(() => {
        const png = pngOf(url);
        // Its failure is the write's; not an unhandled one besides.
        png.catch(() => undefined);
        return navigator.clipboard.write(
            [new ClipboardItem({"image/png": png})]);
    }, IMAGE_COPY_TIMEOUT_MS);
}

// How long an image still to be drawn may take to reach the clipboard: the
// render, behind whatever is being drawn already, and the copy.
export const MADE_IMAGE_COPY_TIMEOUT_MS = 30000;

// Put the image that `url` will name on the clipboard, once there is one
// (canCopyImage() first). The clipboard is asked here, before this returns and
// not a microtask later, as copyImage does: the caller goes on to open another
// window, and the clipboard is given to a page that has the focus. Past the
// deadline the image is refused, so that the write fails and does not come late
// over what the operator copied since.
export function copyImageOnceMade(url: Promise<string>): Promise<void> {
    let writing: Promise<void>;
    try {
        const png = withDeadline(() => url.then(pngOf),
                                 MADE_IMAGE_COPY_TIMEOUT_MS);
        // Its failure is the write's; not an unhandled one besides.
        png.catch(() => undefined);
        writing = navigator.clipboard.write(
            [new ClipboardItem({"image/png": png})]);
    } catch (error) {
        return Promise.reject(error);
    }
    return withDeadline(() => writing, MADE_IMAGE_COPY_TIMEOUT_MS);
}
