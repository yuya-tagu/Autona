// The two outside addresses of the Help & Settings menu, for a page that
// cannot ask the desktop to open them (a browser, the headless app: no shell
// request, ADR-005 S-final SF-5a-3). The desktop's window asks by key
// (openExternal: "discord" / "inquiry") and never names a URL; these are the
// same addresses as src/AutonaExternalLinks.py's (tests/test_app_shell.py,
// tests/test_web_panel_host.py and AppShell.test.js compare them). The About
// page shows the inquiry's address with a copy button.
export const DISCORD_URL = "https://discord.gg/yAFAmhaUqn";
export const BUSINESS_INQUIRY_EMAIL = "taguyangames@gmail.com";
export const BUSINESS_INQUIRY_URL = `mailto:${BUSINESS_INQUIRY_EMAIL}`;
