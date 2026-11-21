// A top 8 bracket on Autona's overlay contract 1 (see AGENTS.md and
// CONTRACT.md). Everything is inside this function: a top-level name could
// clash with globals.js (data, settings, Start, Update...) or a URL parameter.
(function () {
  "use strict";

  // view.top8.matches always has these 11 matches (by id), one column per
  // round. The grand final (gf) and its reset (gf_reset) go at the right
  // end of the winners side; gf_reset shows only when it is played
  // (match.active, which means nothing for the other matches).
  var ROUNDS = {
    winners: [["wsf1", "wsf2"], ["wf"], ["gf"], ["gf_reset"]],
    losers: [["lr1a", "lr1b"], ["lqf1", "lqf2"], ["lsf"], ["lf"]],
  };

  function setText(id, value) {
    document.getElementById(id).textContent = value;
  }

  // Image URLs in the view go straight into src ("" when there is none).
  function setImage(image, url) {
    if (url) {
      image.setAttribute("src", url);
      image.hidden = false;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
    }
  }

  // textContent, never innerHTML: names are typed by people.
  function element(tag, className, text) {
    var node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  // One player of a match: winner is 1 or 2 (null while undecided). slot
  // is null while the player is not decided (every other key is empty).
  function slotRow(player, number, match, options) {
    var row = element("div", "slot", "");
    row.classList.toggle("winner", match.winner === number);
    row.classList.toggle("empty", player.slot === null);
    var icon = document.createElement("img");
    setImage(icon, options.showCharacters ? player.character.icon : "");
    row.appendChild(icon);
    row.appendChild(element("span", "name",
                            player.slot === null ? "TBD" : player.name));
    row.appendChild(element("span", "score",
                            player.score === null ? "" : String(player.score)));
    return row;
  }

  function matchCard(match, options) {
    var card = element("div", "match", "");
    // The state has no "current match": one whose two players are known
    // and that is not finished is taken as being played.
    card.classList.toggle("live", match.p1.slot !== null
                          && match.p2.slot !== null && !match.finished);
    card.appendChild(element("div", "title", match.name));
    card.appendChild(slotRow(match.p1, 1, match, options));
    card.appendChild(slotRow(match.p2, 2, match, options));
    return card;
  }

  function drawSide(id, rounds, view) {
    var side = document.getElementById(id);
    side.replaceChildren();
    rounds.forEach(function (ids) {
      var column = element("div", "round", "");
      ids.forEach(function (matchId) {
        var match = view.top8.matches[matchId];
        if (matchId === "gf_reset" && !match.active) return;
        column.appendChild(matchCard(match, view.options));
      });
      side.appendChild(column);
    });
  }

  Autona.onUpdate(function (view) {
    var tournament = view.tournament;
    setText("tournament", tournament.name);
    setImage(document.getElementById("logo"), tournament.logo);
    setText("phase", view.top8.phaseLabel);
    if (tournament.themeColor) {
      document.documentElement.style.setProperty(
        "--theme", tournament.themeColor);
    }
    if (tournament.accentColor) {
      document.documentElement.style.setProperty(
        "--accent", tournament.accentColor);
    }
    drawSide("winners", ROUNDS.winners, view);
    drawSide("losers", ROUNDS.losers, view);
  });
})();
