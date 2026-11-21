// A commentary bar on Autona's overlay contract 1 (see AGENTS.md and
// CONTRACT.md). Everything is inside this function: a top-level name could
// clash with globals.js (data, settings, Start, Update...) or a URL parameter.
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

  // An element with a class and text. textContent, never innerHTML: the
  // names are typed by people and must show as text.
  function element(tag, className, text) {
    var node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function casterCard(caster, options) {
    var card = element("div", "caster", "");
    card.appendChild(element("div", "role", caster.role));
    var name = element("div", "name", "");
    if (options.showSponsor && caster.sponsor) {
      name.appendChild(element("span", "sponsor", caster.sponsor));
    }
    name.appendChild(element("span", "", caster.name));
    card.appendChild(name);
    // twitter has no leading "@".
    var handle = options.showSocial && caster.twitter ? "@" + caster.twitter : "";
    card.appendChild(element("div", "twitter", handle));
    return card;
  }

  Autona.onUpdate(function (view) {
    var tournament = view.tournament;
    setText("tournament", tournament.name);
    setImage("logo", tournament.logo);
    if (tournament.themeColor) {
      document.documentElement.style.setProperty(
        "--theme", tournament.themeColor);
    }
    if (tournament.accentColor) {
      document.documentElement.style.setProperty(
        "--accent", tournament.accentColor);
    }

    // view.commentary holds the casters to show, in their order (an empty
    // array when nobody is shown).
    var casters = document.getElementById("casters");
    casters.replaceChildren();
    view.commentary.forEach(function (caster) {
      casters.appendChild(casterCard(caster, view.options));
    });
    document.getElementById("bar").hidden = view.commentary.length === 0;
  });
})();
