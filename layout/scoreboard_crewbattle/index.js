/**
 * scoreboard_crewbattle — OBS layout for the CREWBATTLE scoreboard (board 3).
 *
 * Renders both crews as a vertical member strip: each member shows their name
 * and remaining stock; the currently-playing member gets a breathing neon
 * accent outline and eliminated members (0 stock) fade to a faint greyscale.
 * The header shows each team's name and the auto-summed remaining-stock total
 * (score.3.team.{T}.score).
 *
 * OBS browser source URL:
 *   http://<host>:<port>/layout/scoreboard_crewbattle/index.html?scoreboardNumber=3
 * The scoreboardNumber URL param MUST be 3 to follow the CREW board
 * (globals.js defaults to 1).
 */
// Perceived-luminance ink pick for the frosted-glass body text. The glass
// tiles are ~88% --theme-color, so a light theme needs dark text. Uses the
// same YIQ threshold as scoreboard_team/Universal's adaptColorsForTheme
// (dark ink rgb(24,24,27), the shared design token).
function glassInk(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return "#ffffff";
  const v = parseInt(m[1], 16);
  const r = (v >> 16) & 0xff, g = (v >> 8) & 0xff, b = v & 0xff;
  return (r * 299 + g * 587 + b * 114) / 1000 > 140 ? "#18181b" : "#ffffff";
}

LoadEverything().then(() => {
  gsap.config({ nullTargetWarn: false, trialWarn: false });

  // This layout is crew-only (board 3). globals.js defaults scoreboardNumber
  // to 1, which would make the overlay show the 1v1 board (empty crew). Force
  // board 3 unless the OBS URL explicitly passes a different ?scoreboardNumber.
  const _params = new URLSearchParams(window.location.search);
  window.scoreboardNumber = _params.get("scoreboardNumber") || "3";

  Start = async () => {
    // Body starts hidden (CSS opacity:0) so the empty / no-data /
    // font-loading state isn't shown on (re)load. The harness calls Start()
    // only after the first Update() has populated the DOM, so reveal now.
    // Mirrors scoreboard_Universal.
    gsap.set("body", { opacity: 0 });
    gsap.to("body", { opacity: 1, duration: 0.5, ease: "power2.out" });
  };

  // Build (or rebuild) the member rows for a team column when the member
  // count changes. Cheap for <=9 rows, so we just diff on child count.
  const buildRows = (side, count) => {
    const $col = $(`#team${side}`);
    if ($col.children().length === count) return;
    $col.empty();
    for (let m = 1; m <= count; m += 1) {
      $col.append(
        `<div class="member glass" id="t${side}m${m}">` +
          `<span class="mname"><span class="fit"></span></span>` +
          `<span class="mstock"></span>` +
          `</div>`
      );
    }
  };

  // Fit long names by squeezing them horizontally (scaleX) instead of
  // clipping/ellipsising — same idea as globals.js FitText. The .fit span is
  // measured against its container's width; transform-origin (set in CSS)
  // anchors the squeeze to the aligned edge.
  const fitX = (containerEl) => {
    if (!containerEl) return;
    const fit = containerEl.querySelector(".fit");
    if (!fit) return;
    fit.style.transform = "";
    const avail = containerEl.clientWidth;
    const natural = fit.scrollWidth;
    if (avail > 0 && natural > avail) {
      // Always anchor the squeeze to the LEFT (start) edge. When an
      // inline-block is wider than its container, the browser lays it out
      // from the start edge and overflows past the END — regardless of
      // text-align (text-align:right does NOT pull an over-wide box to the
      // right edge). So the box's left edge is the only reliable fixed point.
      // Scaling about it makes the compressed text fill the box exactly
      // (rendered width === avail), which is also what we want for the
      // right-aligned columns. Anchoring to the right edge instead pinned the
      // scale to an off-screen point and shifted the compressed text out of
      // view — only its head stayed visible (the bug this replaces). Assumes
      // LTR, which holds for this tool.
      fit.style.transformOrigin = "left center";
      fit.style.transform = `scaleX(${(avail / natural).toFixed(4)})`;
    } else {
      fit.style.transformOrigin = "";
    }
  };

  // Re-fit whenever a name's natural width changes — crucially when a web-font
  // glyph finishes loading AFTER the text was first measured (Google Fonts
  // lazy-loads Japanese subsets). Without this, the first (synchronous)
  // measurement runs against the fallback font and long names clip instead of
  // squeezing once the real glyphs arrive. scaleX doesn't change layout size,
  // so this never loops.
  const fitObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const container = entry.target.parentElement;
      if (container) fitX(container);
    }
  });
  const observeFit = (containerEl) => {
    const fit = containerEl && containerEl.querySelector(".fit");
    if (fit) fitObserver.observe(fit); // idempotent for already-observed nodes
  };

  Update = async (event) => {
    // Apply theme/accent colors and the tournament name BEFORE the
    // score-data guard below, so they still apply even when this board's
    // score subtree isn't populated yet (e.g. game not selected).
    // 大会カラー drives the frosted-glass tint; accent drives the gold
    // highlights (totals, active marker, phase/round). Falls back to the
    // CSS defaults when the EVENT tab hasn't set a tournament colour.
    const ti = event.data.tournamentInfo || {};
    if (ti.themeColor) {
      document.documentElement.style.setProperty("--theme-color", ti.themeColor);
      // Keep body text readable when the theme is a light colour.
      document.documentElement.style.setProperty(
        "--glass-ink", glassInk(ti.themeColor));
    }
    if (ti.accentColor) {
      document.documentElement.style.setProperty("--accent-color", ti.accentColor);
    }

    // Tournament name (EVENT tab, board-independent) + phase | round
    // (SCORE tab). Empty values collapse via the :empty CSS rule. Prefer the
    // operator-controlled display name; fall back to the raw tournament name
    // only when displayTournamentName was never set (an explicit empty
    // string means "intentionally hidden" and is respected).
    const displayTournamentName = ti.displayTournamentName !== undefined
      ? ti.displayTournamentName
      : (ti.tournamentName || "");
    $("#tournament-name").text(displayTournamentName);

    const sb = event.data.score && event.data.score[window.scoreboardNumber];
    if (!sb || !sb.team) return;

    // Pool is a separate state key (score.N.pool); join it for display.
    const phase = [sb.phase, sb.pool].filter(Boolean).join(" - ");
    const match = sb.match || "";
    $("#phase-round").text([phase, match].filter(Boolean).join("  |  "));

    for (const side of [1, 2]) {
      const team = sb.team[String(side)] || {};
      const memberNum = Number(team.memberNum || 0);
      const active = String(team.activeMember || "0");
      const color = team.color || "";

      // Team colour drives only the thin outer accent edge (the glass body
      // is tinted by the tournament --theme-color).
      if (color) {
        document.documentElement.style.setProperty(`--p${side}-color`, color);
      }

      // Header: team name (squeezed to fit) + auto-summed total.
      $(`#tname${side} .fit`).text(team.teamName || `TEAM ${side}`);
      const tnEl = document.getElementById(`tname${side}`);
      fitX(tnEl);
      observeFit(tnEl);
      $(`#total${side}`).text(team.score != null ? team.score : 0);

      // Member rows.
      buildRows(side, memberNum);
      const players = team.player || {};
      for (let m = 1; m <= memberNum; m += 1) {
        const player = players[String(m)] || {};
        const stock = Number(player.stock || 0);
        const named = String(player.name || "").trim().length > 0;
        const $row = $(`#t${side}m${m}`);
        // Unnamed roster slots render fully blank (no bar, no score).
        $row.toggleClass("empty", !named);
        $row.toggleClass("eliminated", named && stock <= 0);
        $row.toggleClass("active", active === String(m));
        $row.find(".mname .fit").text(player.name || "");
        const mnEl = $row.find(".mname")[0];
        fitX(mnEl);
        observeFit(mnEl);
        $row.find(".mstock").text(stock);
      }
    }
  };
});
