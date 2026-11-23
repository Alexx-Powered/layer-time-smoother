/* The exported bytes and measured layer times belong to one plate and engine revision. */
window.createPlateSmoothingCache = (() => {
  let database, engineHash;
  // These worker builds add curve inputs, with byte-identical G-code output to
  // 00caeca. Pages uses a different ZIP compressor from the local LF/CRLF copies.
  // An unrecognized future engine continues to get its own cache namespace.
  const compatibleExports=new Map([
    ['9c0f42166163c2d51a40b3aae3b181a62b8803484087b9ab4ac7ea5402090f6a','8a625a2984d3e0bccd0be352d656a24b5296ba4b65566bf9b475714c9f6612e7'],
    ['fed157147f3a45b169d459211ebebfb2ce78038cb50dcd58bfa809c63f8ac3f1','6e809312018379fdaa4ab1902c7fab2843064010b0d9577ba1bfe9d4161a96cd'],
    ['0f04ee1404d26ca9443cd79fe38fc2e79ae3f88493e126f1a43ff8dcc3bc9067','44988f2e7dd545e8a29bb2c864885b7b592cdfb2d994148ec34ee2721b279ad1']
  ]);
  const digest = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
    value=>value.toString(16).padStart(2,'0')).join('');
  function open(){
    return database ||= new Promise(resolve=>{
      if(!window.indexedDB){resolve(null);return}
      const request=indexedDB.open('layer-time-smoother',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('results');
      request.onsuccess=()=>resolve(request.result);
      request.onerror=request.onblocked=()=>resolve(null);
    }).catch(()=>null);
  }
  return function(source){
    let address;
    async function locate(){
      if(!globalThis.crypto?.subtle)return null;
      return address ||= (async()=>{
        const db=await open();if(!db)return null;
        engineHash ||= digest(new TextEncoder().encode(LayerSmoothingWorker.cacheSignature()));
        const plate=source.builtin ? layerSmoothingExample.sha256 : await digest(await (await source.file()).arrayBuffer());
        const engine=await engineHash;
        return {db,engine,previous:compatibleExports.get(engine),plate};
      })().catch(()=>null);
    }
    async function access(mode,key,value){
      const location=await locate();if(!location)return;
      return new Promise((resolve,reject)=>{
        const tx=location.db.transaction('results',mode),store=tx.objectStore('results');
        const id=[location.engine,location.plate,key];
        let r=mode==='readonly'?store.get(id):store.put(value,id);
        if(mode==='readonly'&&location.previous)r.onsuccess=()=>{
          if(r.result===undefined)r=store.get([location.previous,location.plate,key]);
        };
        tx.oncomplete=()=>resolve(mode==='readonly'?r.result:true);
        tx.onabort=tx.onerror=()=>reject(tx.error);
      }).catch(()=>undefined);
    }
    return {read:key=>access('readonly',key),write:(key,value)=>access('readwrite',key,value)};
  };
})();
