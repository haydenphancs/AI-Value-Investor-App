import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { readFileSync, readdirSync } from "fs";
import { resolve } from "path";
import { defineConfig, type Plugin } from "vite";

// https://vite.dev/config/
export default defineConfig({
  base: "./",
  plugins: [errorMonitorPlugin(), react(), tailwindcss(), createServeGeneratedCssPlugin()],
  server: {
    port: 3000,
    allowedHosts: true,
  },
  publicDir: "public",
  resolve: {
    alias: [{ find: "@", replacement: "/src" }],
  },
  build: {
    minify: false, // false for readable stack traces
  },
});

type BuildError = {
  message: string;
  stack?: string;
  id?: string;
  plugin?: string;
  loc?: any;
  frame?: string;
  timestamp: number;
};

export function errorMonitorPlugin(): any {
  let currentErrors: BuildError[] = [];
  let lastUpdate = Date.now();

  return {
    name: "error-monitor",
    configureServer(server: any) {
      // Add the healthcheck endpoint (authentication handled by middleware via cookies)
      server.middlewares.use("/__healthcheck", (_req: any, res: any) => {
        res.setHeader("Content-Type", "application/json");
        res.setHeader("Access-Control-Allow-Origin", "*");

        const hasErrors = currentErrors.length > 0;

        res.end(
          JSON.stringify(
            {
              status: hasErrors ? "failed" : "success",
              errors: currentErrors,
              errorCount: currentErrors.length,
              lastUpdate,
              timestamp: Date.now(),
            },
            null,
            2
          )
        );
      });

      // Override the original send method to catch all HMR messages
      const originalSend = server.ws.send;
      server.ws.send = function (payload: any) {
        // Capture error messages
        if (payload && typeof payload === "object" && payload.type === "error") {
          const error = {
            message: payload.err?.message || "Unknown error",
            stack: payload.err?.stack,
            id: payload.err?.id,
            plugin: payload.err?.plugin,
            loc: payload.err?.loc,
            frame: payload.err?.frame,
            timestamp: Date.now(),
          };

          // Add or update error
          const existingIndex = currentErrors.findIndex(e => e.id === error.id);
          if (existingIndex >= 0) {
            currentErrors[existingIndex] = error;
          } else {
            currentErrors.push(error);
          }
          lastUpdate = Date.now();
        }

        // Clear errors on successful updates
        if (payload && typeof payload === "object" && payload.type === "update") {
          currentErrors = [];
          lastUpdate = Date.now();
        }

        return originalSend.call(this, payload);
      };
    },

    // Hook into transform errors
    async transform(_code: any, id: any) {
      try {
        // This will be called for each file transformation
        return null;
      } catch (err) {
        const error = {
          message: err.message,
          stack: err.stack,
          id: id,
          timestamp: Date.now(),
          type: "transform",
        };

        currentErrors.push(error);
        lastUpdate = Date.now();
        throw err; // Re-throw to maintain normal error flow
      }
    },

    // Clear errors on successful builds
    buildStart() {
      currentErrors = [];
      lastUpdate = Date.now();
    },
  };
}
// Custom plugins
// ------------------------------------------------------------

// Serve and emit generated CSS files as static assets
function createServeGeneratedCssPlugin(): Plugin {
  return {
    name: "serve-generated-css",
    configureServer(server) {
      server.middlewares.use(createGeneratedCssMiddleware());
    },
    generateBundle(this) {
      const generatedDir = resolve(__dirname, "src/generated");
      try {
        const files = readdirSync(generatedDir);
        files.forEach(file => {
          if (file.endsWith(".css")) {
            try {
              const content = readFileSync(resolve(generatedDir, file), "utf-8");
              this.emitFile({
                type: "asset",
                fileName: `generated/${file}`,
                source: content,
              });
            } catch (err) {
              console.warn(`Could not copy ${file}:`, err);
            }
          }
        });
      } catch (err) {
        console.warn("Could not read generated directory:", err);
      }
    },
  };
}

// Helpers
// ------------------------------------------------------------
function createGeneratedCssMiddleware() {
  return (req, res, next) => {
    const url = req.url ?? "";
    if (!url.startsWith("/generated/") || !url.endsWith(".css")) {
      return next();
    }

    const fileName = url.slice("/generated/".length);
    const cssPath = resolve(__dirname, `src/generated/${fileName}`);

    try {
      const cssContent = readFileSync(cssPath, "utf-8");
      res.setHeader("Content-Type", "text/css");
      res.end(cssContent);
    } catch (err) {
      console.warn(`Could not read ${fileName}:`, err);
      next();
    }
  };
}
