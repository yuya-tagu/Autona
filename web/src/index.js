import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import reportWebVitals from './reportWebVitals';

import {BrowserRouter, Navigate, Route, Routes} from "react-router-dom";
import ScoreboardPage from "./ScoreboardPage";
import {tshStore} from "./redux/store";
import {Provider} from "react-redux";
import ThemeShell from "./ThemeShell";

// ThemeShell applies the dark MUI theme (the app is dark-only; the
// runtime light/dark toggle was removed).
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
      <Provider store={tshStore}>
          <ThemeShell>
              <BrowserRouter>
                  <Routes>
                      <Route
                          path="/web"
                          element={<ScoreboardPage />}
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

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
