import {styled, TextField as MuiTextField} from "@mui/material";

const StyledTextField = styled(MuiTextField)(({theme}) =>
    ({
        '& label[data-shrink="false"]': {
            color: theme.palette.text.disabled
        }
    })
);

const TextField = (props) => <StyledTextField size="small" {...props} />;

export default TextField;
