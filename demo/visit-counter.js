(() => {
  if (location.origin !== 'https://alexxpowered.top' ||
      !['/layer-time-smoother/', '/layer-time-smoother/index.html'].includes(location.pathname)) return;

  const output = document.getElementById('visitCount');
  const endpoint = 'https://api.alexxpowered.top/layer-time-smoother/visits';
  const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
  const format = new Intl.NumberFormat('zh-CN');
  let registered = false, busy = false, timer, delay = 5000, retryAt = 0;

  async function refresh() {
    clearTimeout(timer);
    if (busy || document.visibilityState !== 'visible') return;
    if (Date.now() < retryAt) {
      timer = setTimeout(refresh, retryAt - Date.now());
      return;
    }
    busy = true;
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 6000);
    try {
      const response = await fetch(registered ? endpoint : endpoint + '?id=' + id, {
        method: registered ? 'GET' : 'POST', cache: 'no-store', credentials: 'omit',
        referrerPolicy: 'no-referrer', signal: abort.signal
      });
      if (response.status === 429) retryAt = Date.now() + 60000;
      if (!response.ok) throw new Error('Visit counter unavailable');
      const { total } = await response.json();
      if (!Number.isSafeInteger(total) || total < 0) throw new Error('Invalid visit count');
      registered = true;
      output.textContent = ' · 累计访问 ' + format.format(total) + ' 次';
      output.hidden = false;
      delay = 5000;
    } catch {
      delay = Math.min(60000, delay * 2);
    } finally {
      clearTimeout(timeout);
      busy = false;
      if (document.visibilityState === 'visible') timer = setTimeout(refresh, Math.max(delay, retryAt - Date.now()));
    }
  }
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('online', refresh);
  refresh();
})();
