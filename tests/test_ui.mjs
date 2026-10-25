import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

// Tiny DOM adapter: tests the real asynchronous unlock controller, not rendering.
class Element {
  constructor() {
    this.children = [];
    this.value = "";
    this.disabled = false;
  }
  addEventListener() {}
  setAttribute(key, value) {
    this[key] = value;
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  focus() {}
}

function app(fetch) {
  const elements = new Map();
  const document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, new Element());
      return elements.get(selector);
    },
    createElement: () => new Element(),
    createTextNode: (text) => text,
    addEventListener() {},
  };
  const context = vm.createContext({ document, fetch, Node: Element, console });
  const source = readFileSync(
    new URL("../ibackup/static/app.js", import.meta.url),
    "utf8",
  ).replace(/boot\(\);\s*$/, "");
  vm.runInContext(
    source +
      "\nglobalThis.controller = { state, unlock, home, mediaFailure: typeof mediaFailure === 'function' ? mediaFailure : undefined, refreshBackups: typeof refreshBackups === 'function' ? refreshBackups : undefined };",
    context,
  );
  return { ...context.controller, document };
}

test("Home during unlock never hides a successful unlocked server session", async () => {
  let finish;
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  const controller = app(() => pending);
  controller.state.selected = {
    id: "fixture",
    name: "Test",
    model: "iPod",
    ios: "9",
    supported: true,
    encrypted: true,
  };
  const button = new Element();
  const form = { querySelector: () => button };
  const opening = controller.unlock({
    preventDefault() {},
    currentTarget: form,
  });
  const epoch = controller.state.epoch;
  controller.home(); // Escape and Home use the same function.
  assert.equal(
    controller.state.epoch,
    epoch,
    "navigation must not cancel a pending unlock result",
  );
  finish({
    ok: true,
    json: async () => ({
      backup: { ...controller.state.selected, unlocked: true },
      generation: 1,
    }),
  });
  await opening;
  assert.equal(controller.state.unlocked, true);
  assert.equal(
    controller.document.querySelector("#lock-button").disabled,
    false,
  );
});

test("media failure reports missing source instead of inventing a codec diagnosis", async () => {
  const controller = app(async () => ({
    ok: false,
    json: async () => ({ error: "Die Datei fehlt." }),
  }));
  assert.equal(
    await controller.mediaFailure({
      id: "fixture",
      source_id: "fixture-backup",
    }),
    "Die Datei fehlt.",
  );
});

test("refresh adds a copied snapshot and preserves the unlocked selection", async () => {
  const current = { id: "now", model: "iPod", ios: "9", device_id: "same", unlocked: true };
  const older = { ...current, id: "old", unlocked: false };
  const controller = app(async (url) => ({
    ok: true,
    json: async () => url.endsWith("/backups")
      ? [current, older]
      : { backup: current, generation: 7, unlocked_count: 1 },
  }));
  controller.state.selected = current;
  controller.state.unlocked = true;
  controller.state.backups = [current];
  await controller.refreshBackups();
  assert.equal(controller.state.backups.length, 2);
  assert.equal(controller.state.selected.id, "now");
  assert.equal(controller.state.unlocked, true);
  assert.equal(controller.state.generation, 7);
  assert.equal(controller.document.querySelector("#refresh-backups").disabled, false);
});
