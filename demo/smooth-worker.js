/* Browser adapter for the repository's Python post-processor. */
(() => {
  "use strict";

  // Replay the same acceleration, junction and axis constraints recorded by
  // gcode_feature_times.estimate, without parsing the entire G-code 23 times
  // while the Python solver searches for each layer's infill feed.
  class MotionCache {
    constructor() {
      this.chunkSize = 16384;
      this.chunks = [];
      this.count = 0;
    }
    add(layer, editable, length, acceleration, feed, factor, speedLimit, junctionLimit, start) {
      const offset = (this.count % this.chunkSize) * 7;
      if (!offset) this.chunks.push(new Float64Array(this.chunkSize * 7));
      const chunk = this.chunks[this.chunks.length - 1];
      chunk[offset] = start ? -1 - layer : layer;
      chunk[offset+1] = editable ? -feed : feed;
      chunk[offset+2] = factor;
      chunk[offset+3] = length;
      chunk[offset+4] = acceleration;
      chunk[offset+5] = speedLimit;
      chunk[offset+6] = junctionLimit;
      this.count++;
    }
    finish(baseline) {
      this.speed = new Float64Array(this.count);
      this.entry = new Float64Array(this.count);
      this.baseline = Float64Array.from(baseline);
      this.originalMotion = this.movement(this.baseline.map(() => 1));
    }
    movement(scales) {
      const seconds = new Float64Array(scales.length);
      let i = 0, previousSpeed = 0;
      for (const chunk of this.chunks) {
        for (let k = 0; k < chunk.length && i < this.count; k += 7, i++) {
          const tag = chunk[k], layer = tag < 0 ? -1 - tag : tag;
          const feed = chunk[k+1] < 0 ? -chunk[k+1] * scales[layer] : chunk[k+1];
          const speed = Math.min(feed * chunk[k+2], chunk[k+5]);
          this.speed[i] = speed;
          this.entry[i] = tag < 0 ? 0 : Math.min(speed, previousSpeed, chunk[k+6]);
          previousSpeed = speed;
        }
      }
      i = this.count;
      let exit = 0;
      for (let part = this.chunks.length - 1; part >= 0; part--) {
        const chunk = this.chunks[part];
        const used = i - part * this.chunkSize;
        for (let k = (used-1)*7; k >= 0; k -= 7) {
          i--;
          const entry = Math.min(this.entry[i], Math.sqrt(exit*exit + 2*chunk[k+4]*chunk[k+3]));
          this.entry[i] = entry;
          exit = chunk[k] < 0 ? 0 : entry;
        }
      }
      i = 0;
      for (const chunk of this.chunks) {
        for (let k = 0; k < chunk.length && i < this.count; k += 7, i++) {
          const tag = chunk[k], layer = tag < 0 ? -1 - tag : tag;
          const length = chunk[k+3], a = chunk[k+4], entry = this.entry[i];
          // Block boundaries and the end of the file both have zero exit speed.
          const exit = i+1 >= this.count ? 0 :
            (this.entry[i+1] = Math.min(this.entry[i+1], Math.sqrt(entry*entry + 2*a*length)));
          const peak = Math.min(this.speed[i], Math.sqrt(a*length + (entry*entry + exit*exit)/2));
          const cruise = Math.max(0, length - (2*peak*peak-entry*entry-exit*exit)/(2*a));
          seconds[layer] += (2*peak-entry-exit)/a + cruise/peak;
        }
      }
      return seconds;
    }
    times(scales) {
      const movement = this.movement(scales);
      return movement.map((time, layer) => this.baseline[layer] + (time - this.originalMotion[layer]));
    }
  }

  function startWorker(Cache, runtimeURL, packed) {
    let python, ready, queue = Promise.resolve();
    function engine() {
      if (!ready) ready = (async () => {
        importScripts(runtimeURL + "pyodide.js");
        python = await loadPyodide({indexURL: runtimeURL});
        python.setStdout({batched: line => console.debug(line)});
        python.FS.writeFile("/engine.zip", Uint8Array.from(atob(packed), c => c.charCodeAt(0)));
        python.runPython('import sys, json\nsys.path.insert(0, "/engine.zip")\nfrom smooth_bridge import Preview');
        return python;
      })().catch(error => { ready = null; throw error; });
      return ready;
    }
    const status = (id, message) => postMessage({type:"status", id, message});
    let loaded = false;
    async function handle({id, type, bytes, strength}) {
      try {
        if (type === "load") {
          loaded = false;
          status(id, "正在加载本机平滑引擎…");
          const py = await engine();
          status(id, "正在读取整盘走线和机器运动参数…");
          self.fastMotion = new Cache();
          py.globals.set("input_buffer", new Uint8Array(bytes));
          try {
            py.runPython('from js import fastMotion\nmodel = Preview(bytes(input_buffer.to_py()), fastMotion)');
          } finally {
            py.globals.delete("input_buffer");
          }
          loaded = true;
          postMessage({type:"loaded", id});
          return;
        }
        if (!loaded) throw Error("请先载入原始切片文件。");
        if (strength !== null && !(Number.isFinite(strength) && strength >= 0 && strength <= 100)) {
          throw Error("平滑度应在 0 到 100 之间。");
        }
        python.globals.set("chosen", strength);
        if (type === "preview" || type === "calculate") {
          const preview = JSON.parse(python.runPython("json.dumps(model.preview(chosen))"));
          postMessage({...preview, type:"preview", id});
          if (type === "preview") return;
        } else if (type !== "export") {
          throw Error("未知的平滑操作。");
        }
        status(id, "正在按实际导出的进给重算各层用时…");
        const result = JSON.parse(python.runPython("json.dumps(model.calculate(chosen))"));
        if (type === "export") {
          const output = python.runPython("model.output");
          try {
            const view = output.getBuffer("u8");
            try {
              const buffer = view.data.slice().buffer;
              postMessage({type:"file", id, bytes:buffer}, [buffer]);
            } finally { view.release(); }
          } finally { output.destroy(); }
        } else {
          postMessage({...result, type:"result", id});
        }
      } catch (error) {
        const full = String(error.message || error).trim();
        const message = full.split("\n").at(-1).replace(/^(?:ValueError|RuntimeError): /, "");
        postMessage({type:"error", id, message});
      }
    }
    self.onmessage = ({data}) => { queue = queue.then(() => handle(data)); };
  }

  const scriptURL = document.currentScript?.src || document.baseURI;
  const runtimeVersion = "0.27.7";
  const runtimeURL = new URL(scriptURL).protocol === "file:" ?
    `https://cdn.jsdelivr.net/pyodide/v${runtimeVersion}/full/` : new URL("runtime/", scriptURL).href;
  globalThis.LayerSmoothingWorker = {
    cacheSignature: () => [globalThis.layerSmoothingPackage,runtimeVersion,MotionCache.toString(),startWorker.toString()].join("\n"),
    create() {
      if (!globalThis.layerSmoothingPackage) throw Error("网页缺少平滑算法包。");
      const source = "(" + startWorker.toString() + ")(" + MotionCache.toString() + "," +
        JSON.stringify(runtimeURL) + "," + JSON.stringify(globalThis.layerSmoothingPackage) + ");";
      const url = URL.createObjectURL(new Blob([source], {type:"text/javascript"}));
      try { return new Worker(url); } finally { URL.revokeObjectURL(url); }
    },
    async getDemoAuto() {
      const cache = globalThis.layerSmoothingExample;
      if (!cache) throw Error("网页缺少示例的自动平滑结果。");
      let bytes = null;
      if (cache.gzip) {
        const zipped = Uint8Array.from(atob(cache.gzip), c => c.charCodeAt(0));
        const stream = new Blob([zipped]).stream().pipeThrough(new DecompressionStream("gzip"));
        bytes = await new Response(stream).arrayBuffer();
      }
      return {result: cache.result, bytes};
    }
  };
})();
