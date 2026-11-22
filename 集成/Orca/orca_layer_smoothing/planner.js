// Browser counterpart of layer_uniformity.curved_targets. Both solve the same
// box-constrained pentadiagonal system; the exporter recomputes it in Python.
function layerTargets(fastest, editable, strength) {
  const result = fastest.slice();
  if (strength === 0) return result;
  const width = 12 * (strength / 100) ** 2;
  for (let start = 1, end = 1; end <= result.length; end++) {
    if (end < result.length && fastest[end] > 0) continue;
    if (end - start > 1) {
      const times = smoothRun(fastest.slice(start, end), editable.slice(start, end), width);
      times.forEach((t, i) => { result[start + i] = t; });
    }
    start = end + 1;
  }
  return result;
}

function smoothRun(times, adjustable, width) {
  const n = times.length, unit = times.reduce((a, b) => Math.max(a, b));
  const floor = times.map(t => t / unit);
  const ceiling = floor.map((t, i) => Math.max(...floor.slice(Math.max(0, i-24), i+25)) - t);
  const movable = adjustable.map((v, i) => v && ceiling[i] > 0);
  const zeros = () => new Float64Array(n);
  const diag = zeros().fill(1), near = zeros(), far = zeros(), rhs = zeros();
  const slope = width ** 2, bend = width ** 4;
  for (let i = 1; i < n; i++) {
    const w = slope + bend * (Number(i === 1) + Number(i === n-1));
    diag[i-1] += w; diag[i] += w; near[i] -= w;
    const delta = w * (floor[i] - floor[i-1]);
    rhs[i-1] += delta; rhs[i] -= delta;
  }
  for (let i = 2; i < n; i++) {
    diag[i-2] += bend; diag[i-1] += 4*bend; diag[i] += bend;
    near[i-1] -= 2*bend; near[i] -= 2*bend; far[i] += bend;
    const delta = bend * (floor[i] - 2*floor[i-1] + floor[i-2]);
    rhs[i-2] -= delta; rhs[i-1] += 2*delta; rhs[i] -= delta;
  }
  function neighbors(i, v) {
    return (i ? near[i]*v[i-1] : 0) + (i >= 2 ? far[i]*v[i-2] : 0)
         + (i+1 < n ? near[i+1]*v[i+1] : 0) + (i+2 < n ? far[i+2]*v[i+2] : 0);
  }
  const free = movable.slice(), uplift = zeros();
  function solve() {
    const d = zeros().fill(1), l1 = zeros(), l2 = zeros(), x = zeros();
    const locked = uplift.map((v, i) => free[i] ? 0 : v);
    for (let i = 0; i < n; i++) {
      if (!free[i]) continue;
      if (i >= 2 && free[i-2]) l2[i] = far[i]/d[i-2];
      if (i && free[i-1]) l1[i] = (near[i]-l2[i]*l1[i-1])/d[i-1];
      d[i] = Math.sqrt(diag[i]-l1[i]**2-l2[i]**2);
      x[i] = (rhs[i]-neighbors(i, locked)-(i ? l1[i]*x[i-1] : 0)-(i >= 2 ? l2[i]*x[i-2] : 0))/d[i];
    }
    for (let i = n-1; i >= 0; i--) {
      if (free[i]) x[i] = (x[i]-(i+1<n ? l1[i+1]*x[i+1] : 0)-(i+2<n ? l2[i+2]*x[i+2] : 0))/d[i];
    }
    return x.map((v, i) => v + locked[i]);
  }
  for (let iteration = 0; iteration < 10*n+100; iteration++) {
    const trial = solve(), blockers = [];
    let step = 1;
    for (let i = 0; i < n; i++) {
      if (!free[i] || (trial[i] >= -1e-11 && trial[i] <= ceiling[i]+1e-11)) continue;
      const bound = trial[i] < 0 ? 0 : ceiling[i];
      const crossing = (bound-uplift[i])/(trial[i]-uplift[i]);
      blockers.push({i, crossing, bound});
      step = Math.min(step, crossing);
    }
    for (let i = 0; i < n; i++)
      uplift[i] = Math.max(0, Math.min(ceiling[i], uplift[i]+step*(trial[i]-uplift[i])));
    if (blockers.length) {
      for (const b of blockers) if (b.crossing <= step+1e-12) {
        free[b.i] = false; uplift[b.i] = b.bound;
      }
      continue;
    }
    let best = 1e-9, release = -1;
    for (let i = 0; i < n; i++) {
      if (free[i] || !movable[i]) continue;
      let pull = rhs[i]-diag[i]*uplift[i]-neighbors(i, uplift);
      if (uplift[i] === ceiling[i]) pull = -pull;
      if (pull > best) { best = pull; release = i; }
    }
    if (release < 0) return times.map((t, i) => uplift[i] > 1e-12 ? t+unit*uplift[i] : t);
    free[release] = true;
  }
  throw new Error("层时间曲线计算未收敛");
}
