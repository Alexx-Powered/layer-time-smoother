// Check the browser worker and its motion cache against CPython's full G-code replay.
// Run from any directory with Node 22+; the shipped Python runtime works offline.
"use strict";
const assert = require("node:assert/strict");
const {createHash} = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {Worker: NodeWorker} = require("node:worker_threads");

const demo = path.join(__dirname, "..", "demo");
const runtime = path.join(demo, "runtime");
const hash = data => createHash("sha256").update(Buffer.from(data)).digest("hex");

function localPyodideWorker(url) {
  // Only the browser's thread and script loader are adapted. Run the same
  // message handlers, WebAssembly, Python modules and downloadable bytes.
  const runtimePath = JSON.stringify(runtime + path.sep);
  const boot = `const {parentPort}=require("node:worker_threads");
    global.self=globalThis;
    global.postMessage=(message, buffers)=>parentPort.postMessage(message,buffers);
    global.importScripts=()=>{global.loadPyodide=()=>require(${runtimePath}+"pyodide.js").loadPyodide({indexURL:${runtimePath}})};
    parentPort.on("message", data=>self.onmessage({data}));`;
  const ready = fetch(url).then(response => response.text()).then(source => {
    const thread = new NodeWorker(boot + source, {eval:true});
    thread.on("message", data => this.onmessage({data}));
    return thread;
  });
  this.postMessage = async (data, buffers) => (await ready).postMessage(data, buffers);
  this.terminate = async () => (await ready).terminate();
}

global.document = {baseURI:"file:///demo/index.html"};
global.Worker = localPyodideWorker;
for (const file of ["smooth-package.js", "smooth-example.js", "smooth-worker.js", "voron-gcode.js"]) {
  vm.runInThisContext(fs.readFileSync(path.join(demo, file), "utf8"), {filename:file});
}

async function verify() {
  const worker = LayerSmoothingWorker.create();
  let id = 0;
  function request(type, values) {
    return new Promise((resolve, reject) => {
      const job = ++id;
      worker.onmessage = ({data}) => {
        if (data.id !== job || data.type === "status" || data.type === "preview") return;
        if (data.type === "error") reject(Error(data.message));
        else resolve(data);
      };
      worker.postMessage({id:job, type, ...values});
    });
  }
  try {
    const bytes = await voronGcodeBytes();
    assert.equal(hash(bytes), layerSmoothingExample.sha256);
    await request("load", {bytes});
    const cached = await LayerSmoothingWorker.getDemoAuto();
    for (const strength of [null, 65.5]) {
      const actual = await request("calculate", {strength});
      const file = await request("export", {strength});
      assert.equal(actual.heights.length, 150);
      if (strength === null) {
        assert.equal(actual.selected, cached.result.selected);
        assert.equal(actual.afterTotal, cached.result.afterTotal);
        assert.deepEqual(actual.after, cached.result.after);
        assert.equal(hash(file.bytes), hash(cached.bytes));
      } else {
        assert.equal(actual.selected, 65.5);
        // Native layer_smoothing.pyz --smoothness 65.5 on the distributed Voron.
        assert.equal(hash(file.bytes), "ee69c48b3edfc438eafa99e6e386aa3521e96a84e0d026cd10e61146bffdf09a");
      }
    }
    console.log("Browser auto/manual exports match native CPython.");
  } finally { await worker.terminate(); }
}

verify().catch(error => {console.error(error);process.exitCode = 1;});
