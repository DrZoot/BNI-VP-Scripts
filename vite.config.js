import { defineConfig } from "vite";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

function localOrExample(local, example) {
  const localPath = fileURLToPath(new URL(local, import.meta.url));
  return existsSync(localPath) ? localPath : fileURLToPath(new URL(example, import.meta.url));
}

export default defineConfig({
  resolve: {
    alias: {
      "@report-data": localOrExample("./output/bni_report.json", "./examples/bni_report.json"),
      "@chapter-config": localOrExample("./config/tradesheet.json", "./config/tradesheet.example.json"),
    },
  },
  server: {
    port: 5173,
    open: false,
  },
  plugins: [
    {
      name: "handlebars-hot-reload",
      handleHotUpdate({ file, server }) {
        if (file.endsWith(".hbs") || file.endsWith(".json")) {
          server.ws.send({
            type: "full-reload",
            path: "*",
          });
        }
      },
    },
  ],
});
