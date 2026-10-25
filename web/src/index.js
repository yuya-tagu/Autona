import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';

import {BrowserRouter, Navigate, Route, Routes} from "react-router-dom";
import ScoreboardPage from "./ScoreboardPage";
import {autonaStore} from "./redux/store";
import {Provider} from "react-redux";
import ThemeShell from "./ThemeShell";
import ScoreboardDesigner from "./ScoreboardDesigner";
import {captureTokenFromUrl} from "./autonaToken";

// Before the router reads the URL: take the access token off it (ADR-005 S3a).
captureTokenFromUrl();

const WebPage = () => (
    new URLSearchParams(window.location.search).get("view") === "designer"
        ? <ScoreboardDesigner />
        : <ScoreboardPage />
);

// ThemeShell applies the dark MUI theme (the app is dark-only; the
// runtime light/dark toggle was removed).
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
      <Provider store={autonaStore}>
          <ThemeShell>
              <BrowserRouter>
                  <Routes>
                      <Route
                          path="/"
                          element={<WebPage />}
                      />
                      <Route
                          path="/web"
                          element={<WebPage />}
                      />
                      <Route
                          path="/scoreboard"
                          element={<ScoreboardPage />}
                      />
                      <Route
                          path="/team"
                          element={<ScoreboardPage scoreboardNumber={2} />}
                      />
                      <Route
                          path="*"
                          element={<Navigate to={"/web"} replace={true}/>}
                      />
                  </Routes>
              </BrowserRouter>
          </ThemeShell>
      </Provider>
  </React.StrictMode>
);

