import {test} from "node:test";
import assert from "node:assert/strict";
import {allowedOrigin, apiUrl, identity} from "../lib/server";

test("rejects cross-site credential requests", () => {
  process.env.FRONTEND_ORIGIN="https://knowledge.example";
  assert.equal(allowedOrigin(new Request("https://knowledge.example/api",{headers:{origin:"https://attacker.example","sec-fetch-site":"cross-site"}})),false);
  assert.equal(allowedOrigin(new Request("https://knowledge.example/api",{headers:{origin:"https://knowledge.example","sec-fetch-site":"same-origin"}})),true);
});
test("upstream address is configured only server-side", () => {
  process.env.RAG_API_URL="https://api.example/";
  assert.equal(apiUrl("/sources"),"https://api.example/sources");
  delete process.env.RAG_API_URL;
  assert.throws(()=>apiUrl("sources"));
});
test("private object authorization requires a bearer session",async()=>{
  await assert.rejects(identity(new Request("https://knowledge.example/api/upload")),/Sign in/);
});
