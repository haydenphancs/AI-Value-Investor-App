// Information for LLM Agent
// This is a component that recieves iframe messages from the user.
// don't update this file!
import { useEffect } from "react";

const processSetCssVars = (cssVars: Record<string, string>) => {
  const fontVars: Record<string, string> = {};
  Object.entries(cssVars).forEach(([varName, value]) => {
    document.documentElement.style.setProperty(varName, value as string);
    if ((varName === "--font-sans" || varName === "--font-serif") && typeof value === "string") {
      fontVars[varName] = value;
    }
  });

  Object.values(fontVars).forEach(fontValue => {
    const fontName = fontValue.split(",")[0]?.replace(/['"]/g, "").trim();

    if (!fontName) {
      return;
    }

    const formattedFont = encodeURIComponent(fontName).replace(/%20/g, "+");
    const href = `https://fonts.googleapis.com/css2?family=${formattedFont}&display=swap`;

    if (document.head.querySelector(`link[data-theme-font="${formattedFont}"]`)) {
      return;
    }

    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.setAttribute("data-theme-font", formattedFont);
    document.head.appendChild(link);
  });
};

const processAppHidden = () => {
  const videoElements = document.querySelectorAll("video");
  videoElements.forEach(video => {
    video.pause();
  });

  const audioElements = document.querySelectorAll("audio");
  audioElements.forEach(audio => {
    audio.pause();
  });

  const youtubeIframes = document.querySelectorAll("iframe");
  youtubeIframes.forEach(iframe => {
    iframe.contentWindow?.postMessage('{"event":"command","func":"stopVideo","args":""}', "*");
  });
};

export const SnMessageReceiver = ({ children }: { children: React.ReactNode }) => {
  useEffect(() => {
    function receive(event: MessageEvent) {
      const { type, cssVars } = event.data || {};

      if (type === "setCssVars" && cssVars && typeof cssVars === "object") {
        processSetCssVars(cssVars);
      } else if (type === "appHidden") {
        processAppHidden();
      }
    }
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);

  return children;
};
