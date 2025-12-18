// This is the main entry point for the application.
// It is responsible for rendering the application and the theme reciever.
// don't update this file!
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./app.tsx";
import { SnPrototype } from "../supernova/helpers/snPrototype.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SnPrototype>
      <App />
    </SnPrototype>
  </StrictMode>
);
