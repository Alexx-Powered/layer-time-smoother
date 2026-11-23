(() => {
  'use strict';
  const names = {bambu: 'Bambu Studio', orca: 'OrcaSlicer'};
  const published = 'https://alexxpowered.top/layer-time-smoother/';
  const base = location.protocol === 'file:' ? published : document.currentScript?.src || location.href;
  const helperURL = new URL('slicer-helper.py', base);
  let paired = '';
  let dialog;

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  async function send(code, app, blob, filename) {
    const parts = /^(\d{1,5})\.([A-Za-z0-9_-]{24})$/.exec(code);
    if (!parts || +parts[1] < 1 || +parts[1] > 65535) throw new Error('请复制完整的配对码，包括中间的点。');
    if (!blob.size || blob.size > 256 * 1024 * 1024) throw new Error('助手支持 256 MiB 以内的文件，请下载 G-code 后拖入切片器。');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120_000);
    let response;
    try {
      response = await fetch('http://127.0.0.1:' + parts[1] + '/open/' + app, {
        method: 'POST', mode: 'cors', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer',
        headers: {'Content-Type': 'application/octet-stream', 'X-Slicer-Token': parts[2], 'X-Gcode-Name': encodeURIComponent(filename.slice(0, 150))},
        body: blob, signal: controller.signal
      });
    } catch {
      throw new Error('浏览器未收到本机助手的回复。请确认助手仍在运行，并允许此页访问本地网络。也可以下载文件后拖入切片器。');
    } finally {
      clearTimeout(timeout);
    }
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.message || ('助手未能打开文件（' + response.status + '）。'));
    }
  }

  function ensureDialog() {
    if (dialog) return dialog;
    const style = document.createElement('style');
    style.textContent = `
      .slicerConnect{box-sizing:border-box;width:min(420px,calc(100vw - 32px));padding:22px;border:1px solid #705a40;
        border-radius:12px;background:#24211e;color:#f5f0e7;font:14px/1.55 var(--sans,system-ui);box-shadow:0 12px 42px #0009}
      .slicerConnect::backdrop{background:#090807a6}
      .slicerConnect h2{margin:0 0 10px;font-size:19px}.slicerConnect p{margin:8px 0;color:#d4c9bb}
      .slicerConnect label{display:block;font-size:13px;margin:12px 0 4px}
      .slicerConnect input{display:block;width:100%;min-height:36px;box-sizing:border-box;padding:6px 8px}
      .slicerConnect code{display:block;overflow-wrap:anywhere;color:#efbf72;margin:6px 0}
      .slicerConnect a{color:#efbf72}.slicerConnect .actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:16px}
      .slicerConnect .result{color:#ffbc84}.slicerConnect [hidden]{display:none}
    `;
    document.head.append(style);
    dialog = document.createElement('dialog');
    dialog.className = 'slicerConnect';
    dialog.setAttribute('aria-labelledby', 'connectSlicerTitle');
    dialog.innerHTML = `
      <h2 id="connectSlicerTitle"></h2>
      <p class="instructions"></p>
      <p><a class="helper" download="slicer-helper.py">下载本机打开助手</a>，使用 Python 3 运行后，将它显示的配对码粘贴到下方。</p>
      <code></code>
      <label for="connectSlicerCode">配对码</label>
      <input id="connectSlicerCode" spellcheck="false" autocomplete="off" autocapitalize="off">
      <p class="result" role="status"></p>
      <div class="actions">
        <button type="button" class="primary connect">配对并打开</button>
        <button type="button" class="save">下载 G-code</button>
        <button type="button" class="close">关闭</button>
      </div>`;
    document.body.append(dialog);
    const helper = dialog.querySelector('.helper');
    helper.href = helperURL.href;
    if (helperURL.origin !== location.origin) {
      helper.addEventListener('click', async (event) => {
        event.preventDefault();
        try {
          const response = await fetch(helperURL, {credentials: 'omit'});
          if (!response.ok) throw new Error();
          download(await response.blob(), 'slicer-helper.py');
        } catch {
          dialog.querySelector('.result').textContent = '助手下载失败，请从在线演示页打开切片器菜单再次下载。';
        }
      });
    }
    return dialog;
  }

  async function open(app, file, filename) {
    if (!Object.hasOwn(names, app)) throw new Error('请选择 Bambu Studio 或 OrcaSlicer。');
    const blob = file instanceof Blob ? file : new Blob([file]);
    let message = '';
    if (paired) {
      try {
        await send(paired, app, blob, filename);
        return {opened: true};
      } catch (error) {
        message = error.message;
      }
    }
    const panel = ensureDialog();
    if (panel.open) return {opened: false};
    const field = panel.querySelector('input');
    const connect = panel.querySelector('.connect');
    const result = panel.querySelector('.result');
    const save = panel.querySelector('.save');
    panel.querySelector('h2').textContent = '在 ' + names[app] + ' 中打开';
    panel.querySelector('.instructions').textContent = '配对后可从此页直接打开本机文件。也可以下载 G-code，再拖入 ' + names[app] + '。';
    const devOrigin = /^http:\/\/(?:localhost|127\.0\.0\.1):\d+$/.test(location.origin);
    const option = location.protocol === 'file:' ? ' --allow-file' : devOrigin ? ' --origin ' + location.origin : '';
    panel.querySelector('code').textContent = 'python slicer-helper.py' + option;
    result.textContent = message;
    field.value = paired;
    connect.disabled = save.disabled = field.disabled = false;
    panel.returnValue = '';
    return new Promise(resolve => {
      let busy = false;
      function sending(value) {
        busy = value;
        field.disabled = connect.disabled = save.disabled = panel.querySelector('.close').disabled = value;
      }
      panel.oncancel = event => {if (busy) event.preventDefault()};
      panel.onclose = () => {
        resolve({opened: panel.returnValue === 'opened', downloaded: panel.returnValue === 'downloaded'});
        connect.onclick = save.onclick = field.onkeydown = panel.onclose = panel.oncancel = panel.querySelector('.close').onclick = null;
      };
      connect.onclick = async () => {
        const code = field.value.trim();
        sending(true);
        result.textContent = '正在把 G-code 交给本机助手…';
        try {
          await send(code, app, blob, filename);
          paired = code;
          panel.close('opened');
        } catch (error) {
          result.textContent = error.message;
        } finally {
          sending(false);
        }
      };
      field.onkeydown = event => {if (event.key === 'Enter') connect.click()};
      save.onclick = () => {download(blob, filename); panel.close('downloaded')};
      panel.querySelector('.close').onclick = () => panel.close();
      panel.showModal();
      field.focus();
    });
  }
  window.SlicerOpen = {open};
})();
