import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";

i18n.use(LanguageDetector).init({
  fallbackLng: ["en"],
  resources: {
    en: {
      translations: require("./locales/en.json"),
    },
    ja: {
      translations: require("./locales/ja.json"),
    },
  },
  detection: {
    order: ["querystring", "navigator"],
    lookupQuerystring: "lng",
  },
  ns: ["translations"],
  defaultNS: "translations",
  debug: true,
  interpolation: { escapeValue: false },
});

i18n.languages = ["en", "ja"];

export default i18n;
