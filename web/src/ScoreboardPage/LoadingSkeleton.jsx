import React from "react";
import {Skeleton, Stack} from "@mui/material";
import {Box} from "@mui/system";

// Initial-load placeholder mirroring the page structure: header bar,
// three columns (team / set controls / team) and the upcoming-sets card.
export default function LoadingSkeleton() {
    return (
        <Box paddingX={1} paddingTop={0.5} paddingBottom={1}>
            <Stack gap={2}>
                <Skeleton variant="rounded" height={96} />
                <Stack direction={{xs: "column", lg: "row"}} gap={1}>
                    <Skeleton variant="rounded" height={320} sx={{flex: 1}} />
                    <Skeleton variant="rounded" height={320} sx={{flex: 1}} />
                    <Skeleton variant="rounded" height={320} sx={{flex: 1}} />
                </Stack>
                <Skeleton variant="rounded" height={180} />
            </Stack>
        </Box>
    );
}
