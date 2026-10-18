import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import PublicChecklistPage from "./pages/PublicChecklistPage.jsx";
import "./index.css";

// /c/{slug}-{token} is the logged-out public checklist page (served by the
// backend's /c/ route): it renders ONLY that checklist's form - never the
// app, its login check or its navigation.
const isPublicChecklist = window.location.pathname.startsWith("/c/");

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>{isPublicChecklist ? <PublicChecklistPage /> : <App />}</React.StrictMode>
);
