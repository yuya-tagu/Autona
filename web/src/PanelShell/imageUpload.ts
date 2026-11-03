// An image a React panel sends to /api/v1 (ADR-005 S4e's Stream page;
// shared with the Event page since S4g): a file the operator chose, read as
// a data URL and sent in a JSON body. The server checks, scales and saves
// it (core/ImageImport.py); the page only refuses what the server would
// refuse for its size, before reading it.

// The largest image the server takes (src/AutonaApiV1.py
// ORG_IMAGE_MAX_BYTES; tests/test_api_v1_stream.py holds the two equal): a
// larger one is refused here before it is sent.
export const IMAGE_UPLOAD_MAX_BYTES = 20 * 1024 * 1024;
export const IMAGE_UPLOAD_ACCEPT =
    "image/png,image/jpeg,image/bmp,.png,.jpg,.jpeg,.bmp";

// An upload of up to 20 MB (27 MB of base64) over the LAN may take longer
// than a write. Not one of the page's queued writes: the page's other
// writes are not held behind it (S4e review).
export const UPLOAD_TIMEOUT_MS = 60000;

export function readDataUrl(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}
