// Headless smoke test of annotate.js against a minimal DOM shim (no browser needed).
// Run by tests/test_ui_smoke.py; argv[2] = JSON boot payload from a real /annotate page.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const boot=fs.readFileSync(process.argv[2],'utf8');
const src=fs.readFileSync(require('path').resolve(__dirname,'../../src/annotool/static/annotate.js'),'utf8');
class El{constructor(t){this.tagName=(t||'').toUpperCase();this.children=[];this.style={cssText:''};this.attrs={};this.listeners={};this.hidden=false;this.className='';this._t='';this.dataset={};
 this.classList={toggle:(c,on)=>{const s=new Set(this.className.split(' ').filter(Boolean));on?s.add(c):s.delete(c);this.className=[...s].join(' ')}}}
 get firstChild(){return this.children[0]||null} get lastChild(){return this.children[this.children.length-1]||null}
 appendChild(c){this.children.push(c);c.parent=this;return c} removeChild(c){this.children.splice(this.children.indexOf(c),1)}
 setAttribute(k,v){this.attrs[k]=v} addEventListener(e,f){(this.listeners[e]=this.listeners[e]||[]).push(f)}
 set textContent(v){this.children=[];this._t=String(v)} get textContent(){return this._t+this.children.map(c=>c.textContent).join('')}
 focus(){} fire(e,ev={}){(this.listeners[e]||[]).forEach(f=>f(Object.assign({target:this,stopPropagation(){}},ev)));if(this['on'+e])this['on'+e](Object.assign({target:this,stopPropagation(){}},ev))}}
class Text{constructor(t){this._t=t} get textContent(){return this._t}}
const byId={};const ids=['boot','savestate','done-count','done-bar','sentence-list','sent-label','tokens','legend','pol','aro','aro-hint','src','none-note','emo-tabs','emo-empty','emo-body','tag-name','tag-toggle','tag-picker','legacy-note','slots','prev','next','mark-none','prev-label','preview','checks','attn-panel','attn','cb-tag','cb-def','help','help-open','help-close'];
ids.forEach(i=>{byId[i]=new El('div')});byId.boot.textContent=boot;byId.savestate.appendChild(new El('i'));byId.savestate.appendChild(new El('span'));
const docL={};const document={getElementById:i=>byId[i],createElement:t=>new El(t),createTextNode:t=>new Text(t),body:new El('body'),addEventListener(e,f){(docL[e]=docL[e]||[]).push(f)},querySelectorAll:()=>[]};
const puts=[];
const fetch=(u,o)=>{puts.push({u,body:JSON.parse(o.body)});return Promise.resolve({ok:true,json:()=>Promise.resolve({status:'done',preview:{},checks:[],has_errors:false,attention:[]})})};
const timers=[];
const ctx={document,window:{addEventListener(){},scrollTo(){}},history:{replaceState(){}},location:{},fetch,setTimeout:(f)=>{timers.push(f);return timers.length},clearTimeout(){},confirm:()=>true,alert(){},console,JSON,Object,Math,Promise};
ctx.window.document=document;vm.createContext(ctx);vm.runInContext(src,ctx);
const all=(e,f,acc=[])=>{if(f(e))acc.push(e);(e.children||[]).forEach(c=>all(c,f,acc));return acc};
const byText=(root,t)=>all(root,e=>e.tagName==='BUTTON'&&e.textContent===t)[0];
const toks=()=>byId.tokens.children;
const tok=(i)=>toks()[i];
// CS000026 is the 6th sentence (index 5), already annotated from HZY file
assert.equal(byId['sent-label'].textContent,'SENTENCE 6 OF 26 · CS000026');
assert(toks().length===19 || toks().length>10);
// slot: cue active; legacy emotion 1 = disappointment. Click a fresh token, then shift-click extends
const em0=()=>JSON.parse(JSON.stringify(puts.length?puts[puts.length-1].body:{}));
// activate cause object slot and add 'it' token 0 -> then toggle off
tok(0).fire('click',{shiftKey:false}); // cue slot: add one-word span at 0
timers.splice(0).forEach(f=>f());
let last=puts[puts.length-1].body;
console.log('cues after click:',JSON.stringify(last.emotions[0].cues));
assert(last.emotions[0].cues.some(s=>s.start===0&&s.end===0));
tok(0).fire('click',{shiftKey:false}); timers.splice(0).forEach(f=>f());
assert(!puts[puts.length-1].body.emotions[0].cues.some(s=>s.start===0&&s.end===0),'click again removes');
// shift-click extends nearest span: existing cue "does not keep me warm" ends at 10; shift-click 12 -> extends to 12
tok(12).fire('click',{shiftKey:true}); timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; console.log('after shift:',JSON.stringify(last.emotions[0].cues));
assert(last.emotions[0].cues.some(s=>s.end===12));
// drag select 14..16
tok(14).fire('mousedown');tok(16).fire('mouseup');tok(16).fire('click',{shiftKey:false});timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; console.log('after drag:',JSON.stringify(last.emotions[0].cues));
assert(last.emotions[0].cues.some(s=>s.start===14&&s.end===16));
// switch to object slot and click 'it' (0): regroup sets group 0
byText(byId.slots,'Cause object')||null;
const slotDivs=byId.slots.children; slotDivs[1].fire('click'); tok(0).fire('click',{});timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; console.log('objects:',JSON.stringify(last.emotions[0].objects));
tok(0).fire('click',{});timers.splice(0).forEach(f=>f());tok(5).fire('click',{});timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; console.log('objects2:',JSON.stringify(last.emotions[0].objects));
assert.deepEqual(last.emotions[0].objects.map(o=>[o.start,o.group]),[[0,0],[5,1]]);
byId.slots.children[2].fire('click'); tok(7).fire('click',{});tok(9).fire('click',{});timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; console.log('actions:',JSON.stringify(last.emotions[0].actions));
assert(last.emotions[0].actions.some(a=>a.start===9&&a.group===1));
// action chip shows an object selector when there are 2+ objects; changing it regroups the action
const actChips=all(byId.slots.children[2],e=>e.tagName==='SELECT'); assert.equal(actChips.length,1,'one selector per action chip');
assert.equal(actChips[0].children.length,2,'one option per object');
actChips[0].value='0'; actChips[0].fire('change'); timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; assert.equal(last.emotions[0].actions[0].group,0,'action moved to object 1');
// adding an object must not silently move that explicit choice
byId.slots.children[1].fire('click'); tok(12).fire('click',{}); timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; assert.equal(last.emotions[0].actions[0].group,0,'explicit assignment survives edits');
// Locked slots: switch emotion 2 (happiness is S2), choose relaxed -> locked
// mark none
byId['mark-none'].onclick(); timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; assert.deepEqual(last.polarity,['none']); assert.equal(last.emotions.length,0);
// polarity click from none: toggle none off then positive creates emotion and opens picker
byText(byId.pol,'none').fire('click'); byText(byId.pol,'positive').fire('click'); timers.splice(0).forEach(f=>f());
last=puts[puts.length-1].body; assert.equal(last.emotions.length,1); assert.equal(last.emotions[0].tag,'');
assert(!byId['tag-picker'].hidden,'picker opens for new emotion');
byText(byId['tag-picker'],'relaxed').fire('click'); timers.splice(0).forEach(f=>f());
assert.equal(puts[puts.length-1].body.emotions[0].tag,'relaxed');
// relaxed is non-Section-2: object slot locked, click ignored
byId.slots.children[1].fire('click'); tok(0).fire('click',{}); timers.splice(0).forEach(f=>f());
assert.deepEqual(puts[puts.length-1].body.emotions[0].objects,[]);
assert(/Not applicable for relaxed/.test(byId.slots.children[1].textContent));
console.log('ALL OK, puts:',puts.length);
