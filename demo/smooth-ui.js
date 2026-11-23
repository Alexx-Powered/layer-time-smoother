/* Compare and export exactly the selection displayed in the panel. */
window.createSmoothingPanel = function ({apply, clear, cancelPreview, inspect}) {
  const el = id => document.getElementById(id);
  const panel = el('smoothPanel'), slider = el('strength'), menu = el('exportMenu');
  const svg = el('timeCurve'), status = el('smoothStatus');
  let source, enabled = false, sourceVersion = 0, selection = 0, strength = null;
  let worker, ready = false, running = null, result, output, chart, layer = 0, timer;
  let requestId = 0, filePromise;
  const gcodeBlob = () => filePromise ||= Promise.resolve().then(source.file);
  function exportReady(available) {
    el('smoothExport').disabled = el('exportMenuButton').disabled = !available;
    if (!available) closeMenu();
  }
  function closeMenu() {
    menu.hidden = true;
    el('exportMenuButton').setAttribute('aria-expanded','false');
  }
  function formatTime(s) {
    const secs = Math.round(Math.abs(s));
    return secs >= 3600 ? Math.floor(secs/3600)+'时 '+Math.floor(secs/60)%60+'分 '+secs%60+'秒' :
      (secs>=60 ? Math.floor(secs/60)+'分 ' : '')+secs%60+'秒';
  }
  function setStatus(text) {status.textContent = text}
  function selectLayer(height) {
    if (!chart?.heights.length) return;
    const heights = chart.heights;
    let lo = 0, hi = heights.length;
    while (lo < hi) {const mid = (lo+hi)>>>1; if (heights[mid]<height) lo=mid+1; else hi=mid}
    const index = Math.min(lo, heights.length-1);
    const next = index>0 && Math.abs(heights[index-1]-height)<Math.abs(heights[index]-height) ? index-1 : index;
    if (next!==layer) {layer=next; markLayer()}
  }
  let chartX = () => 0;
  function markLayer() {
    const cursor = svg.querySelector('line[data-layer]');
    if (!chart || !cursor) return;
    cursor.setAttribute('x1',chartX(layer));cursor.setAttribute('x2',chartX(layer));
    el('layerReadout').textContent='第 '+(layer+1)+' 层　原始 '+chart.before[layer].toFixed(1)+' 秒 · 平滑 '+chart.after[layer].toFixed(1)+' 秒';
  }
  function drawChart() {
    svg.replaceChildren();
    if (!chart?.before.length) return;
    const width = Math.max(250,svg.clientWidth || 680),height=160,left=43,right=width-14,top=17,bottom=133;
    const count=chart.before.length;
    let max=1;
    for (let i=0;i<count;i++) max=Math.max(max,chart.before[i],chart.after[i]);
    const magnitude=10**Math.floor(Math.log10(max/3)),step=Math.ceil(max/3/magnitude)*magnitude;
    max=Math.ceil(max/step)*step;
    chartX = i => left+(right-left)*i/Math.max(1,count-1);
    const y = s => bottom-(bottom-top)*s/max;
    svg.setAttribute('viewBox','0 0 '+width+' '+height);
    function add(tag,attrs,label) {
      const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
      for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,value);
      if(label!==undefined) node.textContent=label;
      svg.append(node);return node;
    }
    for(let v=0;v<=max+.0001;v+=step){
      add('line',{x1:left,x2:right,y1:y(v),y2:y(v),stroke:'#4b443b'});
      add('text',{x:left-6,y:y(v)+3,'text-anchor':'end'},Number(v.toFixed(1))+'s');
    }
    add('text',{x:left,y:151},'第 1 层');
    if(count>1) add('text',{x:right,y:151,'text-anchor':'end'},'第 '+count+' 层');
    for(const [curve,ink,dash] of [[chart.after,'#ffac53',''],[chart.before,'#beb7ae','5 4']]){
      const points=curve.map((s,i)=>chartX(i).toFixed(2)+','+y(s).toFixed(2)).join(' ');
      add('polyline',{points,fill:'none',stroke:ink,'stroke-width':dash?1.4:2,'stroke-dasharray':dash});
      if(count===1) add('circle',{cx:chartX(0),cy:y(curve[0]),r:2.5,fill:ink});
    }
    add('line',{'data-layer':'',y1:top,y2:bottom,stroke:'#ecbf79','stroke-opacity':.55});
    layer=Math.min(layer,count-1);markLayer();
  }
  svg.addEventListener('click',event=>{
    if(!chart)return;
    const x=event.clientX-svg.getBoundingClientRect().left;
    const i=Math.min(chart.heights.length-1,Math.max(0,Math.round((x-chartX(0))/(chartX(1)-chartX(0))||0)));
    inspect(chart.heights[i]);
  });
  new ResizeObserver(drawChart).observe(svg);
  function display(r,provisional) {
    chart=r;drawChart();
    const delta=r.afterTotal-r.beforeTotal;
    el('smoothTotal').textContent=formatTime(r.afterTotal);
    const sign=delta<-.05?'−':delta>.05?'+':'';
    el('smoothCost').textContent=sign+formatTime(delta)+'（'+sign+(r.beforeTotal?Math.abs(100*delta/r.beforeTotal):0).toFixed(1)+'%）';
    if(strength===null){
      el('strengthValue').textContent=r.selected==null?'自动 · 保留原速':'自动 · '+r.selected;
      if(r.selected!=null) slider.value=r.selected;
    }
    if(provisional)setStatus('调速预估 · 正在重算导出后的走线。');
  }
  function resetResult(){
    chart=null;result=null;output=null;exportReady(false);drawChart();
    el('smoothTotal').textContent=el('smoothCost').textContent='—';
    el('layerReadout').textContent='';
  }
  function stopWorker(){
    if(worker)worker.terminate();
    worker=null;ready=false;running=null;
  }
  async function accept(r,blob,revision){
    if(!enabled||revision!==selection)return;
    result=r;output=blob;display(r,false);exportReady(true);
    const message=r.changed?'已按导出指令重算。':'本次保留原始进给。';
    setStatus(message+(r.changed?' 正在更新 3D 预览…':''));
    try{
      await apply(blob,r,()=>enabled&&revision===selection);
      if(enabled&&revision===selection)setStatus(message);
    }catch(error){
      if(enabled&&revision===selection)setStatus(message+' 3D 预览未更新：'+error.message);
    }
  }
  function fail(message){resetResult();clear();setStatus(message+'　可点「自动选参」重试。')}
  function queueCompute(){
    if(!enabled||!ready||running||result)return;
    if(source.builtin&&strength===null&&window.LayerSmoothingWorker?.getDemoAuto)return;
    const id=++requestId;running={id,revision:selection,strength,result:null};
    worker.postMessage({type:'calculate',id,strength});
  }
  async function begin(revision){
    try {
      if(source.builtin && strength===null && window.LayerSmoothingWorker?.getDemoAuto){
        const cached=await window.LayerSmoothingWorker.getDemoAuto();
        if(revision!==selection||!enabled)return;
        const blob=cached.bytes?new Blob([cached.bytes]):await gcodeBlob();
        await accept(cached.result,blob,revision);return;
      }
      if(worker){queueCompute();return}
      const owner=sourceVersion,w=window.LayerSmoothingWorker.create();worker=w;
      w.onmessage=({data:m})=>{
        if(worker!==w||sourceVersion!==owner||!enabled)return;
        if(m.type==='loaded'){ready=true;queueCompute();return}
        if(m.type==='status'){if(!running||running.revision===selection)setStatus(m.message);return}
        if(m.type==='error'){
          if(!running){stopWorker();fail(m.message);return}
          const current=running.revision===selection;running=null;
          if(current)fail(m.message);else queueCompute();return;
        }
        if(!running||m.id!==running.id)return;
        if(m.type==='preview'){if(running.revision===selection)display(m,true);return}
        if(m.type==='result'){
          if(running.revision!==selection){running=null;queueCompute();return}
          running.result=m;display(m,false);
          w.postMessage({type:'export',id:m.id,strength:running.strength});return;
        }
        if(m.type==='file'){
          const done=running;running=null;
          if(done.revision===selection)void accept(done.result,new Blob([m.bytes],{type:'application/octet-stream'}),done.revision);
          else queueCompute();
        }
      };
      w.onerror=e=>{if(worker===w){stopWorker();fail(e.message||'平滑引擎启动失败。')}};
      const bytes=await (await gcodeBlob()).arrayBuffer();
      if(worker!==w||sourceVersion!==owner)return;
      w.postMessage({type:'load',id:++requestId,bytes},[bytes]);
    }catch(error){if(revision===selection&&enabled){stopWorker();fail(error.message)}}
  }
  function recalculate(debounce=false){
    const revision=++selection;
    clearTimeout(timer);cancelPreview();resetResult();
    el('smoothAuto').setAttribute('aria-pressed',String(strength===null));
    el('strengthValue').textContent=strength===null?'自动':String(strength);
    setStatus(strength===null?'正在为这一盘自动选参…':'正在计算平滑度 '+strength+'…');
    timer=setTimeout(()=>begin(revision),debounce?180:0);
  }
  function toggle(value){
    enabled=value;el('smoothSwitch').setAttribute('aria-checked',String(value));panel.hidden=!value;
    if(value){strength=null;recalculate();return}
    selection++;clearTimeout(timer);stopWorker();cancelPreview();resetResult();clear();
  }
  el('smoothSwitch').onclick=()=>toggle(!enabled);
  slider.oninput=()=>{strength=slider.valueAsNumber;recalculate(true)};
  el('smoothAuto').onclick=()=>{strength=null;recalculate()};
  function name(){
    const suffix=strength===null?'auto':String(strength);
    return source.name+'-smooth-'+suffix+'.gcode';
  }
  el('smoothExport').onclick=()=>{
    if(!output)return;
    const a=document.createElement('a'),url=URL.createObjectURL(output);
    a.href=url;a.download=name();a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
  };
  el('exportMenuButton').onclick=()=>{
    menu.hidden=!menu.hidden;el('exportMenuButton').setAttribute('aria-expanded',String(!menu.hidden));
    if(!menu.hidden)menu.querySelector('button').focus();
  };
  menu.onclick=async e=>{
    const button=e.target.closest('[data-slicer]');if(!button||!output)return;
    closeMenu();el('exportMenuButton').focus();
    await window.SlicerOpen.open(button.dataset.slicer,output,name());
  };
  document.addEventListener('click',e=>{if(!e.target.closest('.exportWrap'))closeMenu()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!menu.hidden){closeMenu();el('exportMenuButton').focus()}});
  return {setSource(next){
    sourceVersion++;selection++;clearTimeout(timer);stopWorker();cancelPreview();filePromise=null;
    source=next;strength=null;slider.value=65;resetResult();
    if(enabled)recalculate();
  },selectLayer};
};
