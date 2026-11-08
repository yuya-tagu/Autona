import React from "react";

// Whether the page is shown, and how many times it was shown again (the
// Thumbnail page's, S4p; the Result page's, S4r). The reveals are counted where
// the browser says so: React may take a hide and the show after it as one
// update and never render the page hidden (the desktop's view freezes a hidden
// page; one frozen before it rendered its hide runs both once thawed).
export function useDocumentVisibility(): {visible: boolean; reveals: number} {
    // "prerender" and the like count as shown: only "hidden" holds a page's
    // work back.
    const read = () => document.visibilityState !== "hidden";
    const [visible, setVisible] = React.useState(read);
    const [reveals, setReveals] = React.useState(0);
    React.useEffect(() => {
        let shown = read();
        const changed = () => {
            const now = read();
            if (now && !shown) {
                setReveals((count) => count + 1);
            }
            shown = now;
            setVisible(now);
        };
        document.addEventListener("visibilitychange", changed);
        return () => document.removeEventListener("visibilitychange", changed);
    }, []);
    return {visible, reveals};
}
