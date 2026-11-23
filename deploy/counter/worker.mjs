const path = '/layer-time-smoother/visits';
const readTotal = 'SELECT total FROM visit_total WHERE id = 1';
const addVisit = 'INSERT INTO visits (id) VALUES (?) ON CONFLICT (id) DO NOTHING';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== path) return new Response(null, { status: 404 });

    const origin = request.headers.get('Origin');
    const allowed = origin !== null &&
      origin === env.SITE_ORIGIN;
    const headers = { 'Cache-Control': 'no-store', Vary: 'Origin' };
    if (allowed) headers['Access-Control-Allow-Origin'] = origin;
    const reply = (data, status = 200, extraHeaders) =>
      Response.json(data, { status, headers: { ...headers, ...extraHeaders } });

    const writing = request.method === 'POST';
    if (!writing && request.method !== 'GET') {
      return reply({ error: 'method_not_allowed' }, 405, { Allow: 'GET, POST' });
    }
    if ((writing || origin) && !allowed) return reply({ error: 'origin_forbidden' }, 403);

    const id = url.searchParams.get('id');
    if (writing && !/^[a-f0-9]{32}$/.test(id ?? '')) return reply({ error: 'invalid_visit' }, 400);

    try {
      const limiter = writing ? env.VISIT_WRITE_LIMIT : env.VISIT_READ_LIMIT;
      const key = request.headers.get('CF-Connecting-IP') || 'unknown';
      if (!(await limiter.limit({ key })).success) {
        return reply({ error: 'too_many_requests' }, 429, { 'Retry-After': '60' });
      }

      const select = env.DB.prepare(readTotal);
      // D1 batches run as one transaction. The trigger increments only for a new ID.
      const total = writing
        ? (await env.DB.batch([env.DB.prepare(addVisit).bind(id), select]))[1].results[0]?.total
        : await select.first('total');
      if (!Number.isSafeInteger(total) || total < 0) throw new Error('Missing visit total');
      return reply({ total });
    } catch (error) {
      console.error('Visit counter unavailable', error.message);
      return reply({ error: 'counter_unavailable' }, 503);
    }
  },
};
