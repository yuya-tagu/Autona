"""The addresses the desktop opens outside the app.

The Help & Settings menu's "Join the Discord community" and "Event support
inquiries" (src/Autona.py) open them, and so does the shared view's shell
request openExternal (ADR-005 S-final SF-5a-1; AutonaShellOperations): by
its key in EXTERNAL_LINKS -- the page names a key and never a URL, so
nothing else is opened that way.

BUSINESS_INQUIRY_EMAIL is the About page's too (web/src/GuidePages/
AboutPage.tsx; tests/test_web_panel_host.py compares the two), and TERMS.md
section 4's (kept in sync by hand). They were src/Autona.py's until SF-5a-1.

Import side effects: none (agent-pitfalls #1).
"""

DISCORD_URL = "https://discord.gg/yAFAmhaUqn"
BUSINESS_INQUIRY_EMAIL = "taguyangames@gmail.com"
BUSINESS_INQUIRY_URL = f"mailto:{BUSINESS_INQUIRY_EMAIL}"

# openExternal's targets (ADR-005 S-final SF-5a-1): key -> address. A shell
# request naming any other key is refused before it runs
# (AutonaWebPanelHost.ParseShellRequest).
EXTERNAL_LINKS = {
    "discord": DISCORD_URL,
    "inquiry": BUSINESS_INQUIRY_URL,
}
