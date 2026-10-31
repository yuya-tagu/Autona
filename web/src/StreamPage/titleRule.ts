// The stream title rule (ADR-005 S4d / S4e): the TypeScript twin of core's
// StreamService.TitleIsAuto / TypedTitleWins (src/core/StreamService.py).
// Both are held to one table, titleRule.cases.json, by tests on each side
// (tests/test_stream_service.py, titleRule.test.js): change the rule in
// both places and the table with them.

type Text = string | null | undefined;

// Whether `title` (stream.title) is the auto-generated one: what the
// generator wrote last (`titleAuto`, stream.titleAuto) or nothing. No
// titleAuto (a state from before S4d): the title counts as auto.
export function titleIsAuto(title: Text, titleAuto: Text): boolean {
    if (titleAuto === null || titleAuto === undefined) {
        return true;
    }
    const current = title || "";
    return !current || current === titleAuto;
}

// Whether the page writes what the operator typed into the title field.
// `shown`: what the field last showed in step with the state. Not when the
// text is the state's already, nor when the operator did not change what
// was shown; otherwise yes over the title they started from or an auto
// title (the generator never beats a hand), but not over another writer's
// own title that landed meanwhile.
export function typedTitleWins(typed: Text, shown: Text, title: Text,
                               titleAuto: Text): boolean {
    const text = typed || "";
    const current = title || "";
    if (text === current || text === (shown || "")) {
        return false;
    }
    return current === (shown || "") || titleIsAuto(current, titleAuto);
}
