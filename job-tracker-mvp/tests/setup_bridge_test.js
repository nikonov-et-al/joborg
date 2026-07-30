const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(0, "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

function page(api) {
  const ids = [
    "message", "browse", "submitButton", "cancel", "folder", "migration",
    "title", "intro", "folderHint", "legacy", "setupForm", "profileFields",
    "workspaceNameField", "workspaceNameHint",
  ];
  const names = [
    "given_name", "family_name", "email", "phone", "city", "linkedin",
    "github", "website", "workspace_name",
  ];
  const element = () => ({
    value: "",
    textContent: "",
    disabled: true,
    hidden: false,
    readOnly: false,
    listeners: {},
    addEventListener(type, listener) { this.listeners[type] = listener; },
  });
  const elements = Object.fromEntries(ids.map(id => [id, element()]));
  const named = Object.fromEntries(names.map(name => [name, element()]));
  const windowListeners = {};
  const timers = [];
  const window = {
    pywebview: api ? { api } : undefined,
    addEventListener(type, listener) { windowListeners[type] = listener; },
  };
  const document = {
    querySelector(selector) {
      if (selector.startsWith("#")) return elements[selector.slice(1)];
      const match = selector.match(/^\[name="(.+)"\]$/);
      return match ? named[match[1]] : null;
    },
    querySelectorAll(selector) {
      return selector === "#profileFields input" ? Object.values(named) : [];
    },
  };
  const context = vm.createContext({
    clearTimeout(timer) { timer.cancelled = true; },
    console,
    document,
    setTimeout(callback) {
      const timer = { callback, cancelled: false };
      timers.push(timer);
      return timer;
    },
    window,
  });
  vm.runInContext(script, context);

  return {
    elements,
    named,
    timers,
    window,
    async dispatch(type) {
      await windowListeners[type]();
      await Promise.resolve();
    },
    async settle(limit = 100) {
      for (let count = 0; count < limit; count += 1) {
        await Promise.resolve();
        const timer = timers.shift();
        if (!timer) {
          await Promise.resolve();
          if (!timers.length) return;
          continue;
        }
        if (!timer.cancelled) await timer.callback();
      }
      throw new Error("setup bridge timers did not settle");
    },
  };
}

const setupApi = overrides => ({
  get_setup_state: async () => ({ mode: "setup", personal: {} }),
  choose_workspace_folder: async () => "",
  configure_data_folder: async () => ({}),
  recover_data_folder: async () => ({}),
  create_workspace: async () => ({}),
  cancel_setup: async () => {},
  ...overrides,
});

test("controls start disabled", () => {
  for (const id of ["browse", "submitButton", "cancel"]) {
    assert.match(html, new RegExp(`<button id="${id}"[^>]* disabled>`));
  }
});

test("immediate API availability initializes controls", async () => {
  const view = page(setupApi());
  await view.settle();
  assert.equal(view.elements.browse.disabled, false);
  assert.equal(view.elements.submitButton.disabled, false);
  assert.equal(view.elements.cancel.disabled, false);
});

test("delayed pywebviewready initializes controls", async () => {
  const view = page();
  view.window.pywebview = { api: setupApi() };
  await view.dispatch("pywebviewready");
  await view.settle();
  assert.equal(view.elements.browse.disabled, false);
});

test("missed event retry initializes controls", async () => {
  const view = page({});
  await Promise.resolve();
  Object.assign(view.window.pywebview.api, setupApi());
  await view.settle();
  assert.equal(view.elements.browse.disabled, false);
});

test("chooser depends only on chooser method", async () => {
  const view = page({ choose_workspace_folder: async () => "" });
  await view.settle();
  assert.equal(view.elements.browse.disabled, false);
  assert.equal(view.elements.submitButton.disabled, true);
  assert.equal(view.elements.cancel.disabled, true);
});

test("legacy migration explains copy and recovery behavior", async () => {
  const view = page(setupApi({
    get_setup_state: async () => ({
      mode: "migrate",
      legacy_workspace: "C:\\legacy-workspace",
      personal: {},
    }),
    configure_data_folder: async () => ({}),
  }));
  await view.settle();
  assert.equal(view.elements.submitButton.disabled, false);
  assert.equal(view.elements.browse.disabled, false);
  assert.equal(view.elements.migration.hidden, false);
  assert.equal(view.elements.submitButton.textContent, "Migrate and open workspace");
  assert.match(view.elements.migration.textContent, /C:\\legacy-workspace/);
  assert.match(view.elements.migration.textContent, /copied and verified/);
  assert.match(view.elements.migration.textContent, /retained for recovery/);
});

test("same-folder adoption is prefilled and sends the optional workspace name", async () => {
  let configured = null;
  const root = "C:\\Users\\person\\AppData\\Local\\jobtracker";
  const view = page(setupApi({
    get_setup_state: async () => ({
      mode: "migrate",
      legacy_workspace: root,
      suggested_data_folder: root,
      same_folder_adoption: true,
      personal: {
        given_name: "Jane",
        family_name: "Example",
        email: "jane@example.com",
      },
    }),
    configure_data_folder: async (...args) => { configured = args; },
  }));
  await view.settle();
  assert.equal(view.elements.folder.value, root);
  assert.equal(view.elements.submitButton.textContent, "Organize existing workspace here");
  assert.match(view.elements.folderHint.textContent, /recovery copy/);
  view.named.workspace_name.value = "Germany search";
  await view.elements.setupForm.listeners.submit({ preventDefault() {} });
  assert.equal(configured[0], root);
  assert.equal(configured[1].given_name, "Jane");
  assert.equal(configured[1].family_name, "Example");
  assert.equal(configured[1].email, "jane@example.com");
  assert.equal(configured[2], "Germany search");
});

test("recovery uses an existing managed data folder", async () => {
  let recovered = "";
  const view = page({
    get_setup_state: async () => ({
      mode: "recovery",
      error: "Configured folder is unavailable",
      personal: {},
    }),
    recover_data_folder: async path => { recovered = path; },
  });
  await view.settle();
  view.elements.folder.value = "D:\\job-tracker-data";
  await view.elements.setupForm.listeners.submit({ preventDefault() {} });
  assert.equal(recovered, "D:\\job-tracker-data");
  assert.equal(view.elements.profileFields.hidden, true);
  assert.equal(view.named.given_name.required, false);
});

test("setup-state failure leaves independent actions usable", async () => {
  const view = page(setupApi({
    get_setup_state: async () => { throw new Error("Setup state failed"); },
  }));
  await view.settle();
  assert.equal(view.elements.message.textContent, "Setup state failed");
  assert.equal(view.elements.browse.disabled, false);
  assert.equal(view.elements.cancel.disabled, false);
});

test("missing integration reports friendly error", async () => {
  const view = page();
  await view.settle();
  assert.equal(
    view.elements.message.textContent,
    "Desktop integration unavailable. Restart Job Tracker.",
  );
});

test("repeated selection replaces displayed path", async () => {
  const selections = ["C:\\folder-a", "D:\\folder-b"];
  const view = page(setupApi({
    choose_workspace_folder: async () => selections.shift(),
  }));
  await view.settle();
  await view.elements.browse.listeners.click();
  assert.equal(view.elements.folder.value, "C:\\folder-a");
  await view.elements.browse.listeners.click();
  assert.equal(view.elements.folder.value, "D:\\folder-b");
  assert.equal(view.elements.browse.disabled, false);
});
