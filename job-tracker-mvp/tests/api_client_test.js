import assert from "node:assert/strict";
import test from "node:test";
import { createApiClient, tokenFromLocation } from "../jobtracker/web/static/api-client.js";

test("client owns token header and leaves FormData content type browser-managed", async () => {
  let call;
  const client = createApiClient({ token: "real", fetchFn: async (...args) => {
    call = args;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  } });
  await client.request("/files", { method: "POST", body: new FormData(), headers: { "x-jobtracker-token": "fake", "Content-Type": "wrong" } });
  assert.equal(call[1].headers.get("X-Jobtracker-Token"), "real");
  assert.equal(call[1].headers.get("Content-Type"), null);
});

test("token removal retains other query and hash", () => {
  let replaced = "";
  const locationRef = { href: "https://example.test/app?keep=1&token=secret#jobs" };
  const historyRef = { replaceState: (_a, _b, url) => { replaced = url; } };
  assert.equal(tokenFromLocation(locationRef, historyRef), "secret");
  assert.equal(replaced, "/app?keep=1#jobs");
});

test("binary downloads use authenticated client", async () => {
  let headers;
  let clicked = false;
  const originalDocument = globalThis.document;
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  globalThis.document = {
    createElement: () => ({ click() { clicked = true; }, remove() {} }),
    body: { append() {} },
  };
  URL.createObjectURL = () => "blob:test";
  URL.revokeObjectURL = () => {};
  try {
    const client = createApiClient({ token: "real", fetchFn: async (_url, options) => {
      headers = options.headers;
      return new Response(new Blob(["pdf"]), { status: 200 });
    } });
    await client.download("/report/pdf", "report.pdf");
  } finally {
    globalThis.document = originalDocument;
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
  assert.equal(headers.get("X-Jobtracker-Token"), "real");
  assert.equal(headers.get("Accept"), "application/pdf");
  assert.equal(clicked, true);
});
