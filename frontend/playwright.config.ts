import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", use: {channel: "chrome", baseURL: "http://127.0.0.1:3000", viewport: {width:1440,height:1000}},
  webServer: {command: "npm run start -- --hostname 127.0.0.1", url:"http://127.0.0.1:3000", reuseExistingServer:true, timeout:60_000},
});
