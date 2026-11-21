// A scoreboard on Autona's overlay contract 1 (see AGENTS.md and CONTRACT.md).
// Everything is inside this function: a top-level name could clash with
// globals.js (data, settings, Start, Update...) or a URL parameter.
(function () {
  "use strict";

  function setText(id, value) {
    document.getElementById(id).textContent = value;
  }

  // Image URLs in the view go straight into src ("" when there is none).
  function setImage(id, url) {
    var image = document.getElementById(id);
    if (url) {
      image.setAttribute("src", url);
      image.hidden = false;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
    }
  }

  function drawSide(side, team, options) {
    // A team shows its name; a single player (or a team without one) the
    // player's name.
    setText(side + "-name", team.teamName || team.name);
    setText(side + "-sponsor", options.showSponsor ? team.sponsor : "");
    // score is null before anything is entered.
    setText(side + "-score", team.score === null ? "0" : String(team.score));
    setImage(side + "-flag", options.showLocation ? team.country.flag : "");
    setImage(side + "-character",
             options.showCharacters ? team.character.icon : "");
    document.getElementById(side).classList.toggle("losers", team.losers);
    if (team.color) {
      document.documentElement.style.setProperty(
        "--" + side + "-color", team.color);
    }
    // In a team match, every member's name under the team's.
    var members = team.players.length > 1
      ? team.players.map(function (player) { return player.name; })
      : [];
    setText(side + "-members", members.join(" / "));
  }

  Autona.onUpdate(function (view) {
    var board = view.board;
    var tournament = view.tournament;

    setText("tournament", tournament.name);
    setImage("logo", tournament.logo);
    setText("phase", board.phase);
    setText("round", board.round);
    setText("best-of", board.bestOf === null ? "" : "Best of " + board.bestOf);
    if (tournament.themeColor) {
      document.documentElement.style.setProperty(
        "--theme", tournament.themeColor);
    }
    if (tournament.accentColor) {
      document.documentElement.style.setProperty(
        "--accent", tournament.accentColor);
    }

    drawSide("p1", board.p1, view.options);
    drawSide("p2", board.p2, view.options);
  });
})();
