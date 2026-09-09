"""Offline recipe editor served by the coater's own Wi-Fi access point."""

PAGE = b'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gotham | Spin recipes</title><style>
:root{color-scheme:dark;--bg:#101719;--panel:#182326;--line:#344347;--text:#edf4ef;--muted:#a3b5b5;--accent:#75e0c9;--red:#ff918a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}main{max-width:1080px;margin:auto;padding:28px 20px 50px}
header{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}.eyebrow{color:var(--accent);letter-spacing:.18em;font-size:11px;font-weight:750;text-transform:uppercase}h1{font-size:clamp(26px,5vw,38px);line-height:1.15;margin:6px 0}h2{font-size:18px;margin:0 0 16px}p{margin:8px 0;color:var(--muted)}.pill{padding:6px 12px;border:1px solid var(--line);border-radius:30px;white-space:nowrap;color:var(--accent)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:18px}.layout{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:18px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row.between{justify-content:space-between}.fill{flex:1;min-width:120px}.muted,small{color:var(--muted)}.small{font-size:12px}.statusline{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap}.statusline strong{font-size:24px}.fans{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:16px}.fan{border:1px solid var(--line);border-radius:10px;padding:10px;min-width:0}.fan.off{background:#780e1d;border-color:#bd3a50}.fan strong{display:block;font-size:22px;font-variant-numeric:tabular-nums}.fan .bar{height:4px;background:var(--line);margin-top:8px;border-radius:5px;overflow:hidden}.fan .bar i{display:block;height:100%;background:var(--accent)}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}input,select,button{font:inherit;border:1px solid var(--line);border-radius:8px;padding:9px 11px;max-width:100%;color:var(--text);background:#111b1e}input,select{width:100%}button{cursor:pointer;white-space:nowrap}button:hover{border-color:var(--accent)}button.primary{background:var(--accent);color:#092a24;border-color:var(--accent);font-weight:750}button.danger{color:var(--red)}button:disabled{opacity:.4;cursor:default}input:focus,select:focus,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}input:disabled{color:var(--muted);background:transparent}fieldset{padding:0;margin:0;border:0;min-width:0}.field{margin:14px 0}.field small{display:block;margin-top:5px;font-size:12px}
.steps{width:100%;border-collapse:collapse;margin:18px 0}.steps th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);padding:6px}.steps td{padding:5px}.steps td:first-child{width:26px;color:var(--muted);font-size:12px}.steps td:last-child{width:32px}.steps button{padding:8px;color:var(--red)}.steps input{min-width:65px;padding:9px 7px}.steps th:nth-child(2){width:34%}svg{display:block;width:100%;height:126px;background:#111b1e;border-radius:10px}#notice{min-height:23px;margin:12px 0 0;color:var(--accent)}#notice.error{color:var(--red)}.footer-actions{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:20px}.settings label{color:var(--text)}.settings .unit{font-size:12px;color:var(--muted)}
.fan.warning{background:#600820;border-color:var(--red)}.fan.warning .bar i{background:var(--red)}.rpm-warning{display:block;margin-top:4px;color:var(--red);font-size:11px;overflow-wrap:anywhere}
.driver-error{display:block;margin-top:4px;color:var(--muted);font-size:11px;overflow-wrap:anywhere}
@media(max-width:780px){.layout{grid-template-columns:1fr}.fans{grid-template-columns:repeat(3,1fr)}header{align-items:flex-start}.panel{padding:16px}.settings{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}.settings h2,.settings p{grid-column:1/-1}}
@media(max-width:430px){main{padding:20px 12px}.steps th{font-size:10px;padding:4px}.steps td{padding:3px}.steps input{min-width:50px}.pill{font-size:12px}.settings{display:block}}
</style></head><body><main>
<header><div><div class="eyebrow">Gotham / six-channel spin coater</div><h1>Make every spin repeatable.</h1><p>Build a recipe. Save it to the coater. Start at the bench.</p></div><div id="connection" class="pill">Connecting...</div></header>
<section class="panel" aria-label="Live coater status"><div class="statusline"><div><span class="eyebrow" id="run-state">IDLE</span><br><strong id="target">0 RPM</strong> <span class="muted">target</span></div><div><div id="phase" class="muted">Ready</div><div id="run-message" class="small muted">Use the left START button on the coater.</div></div></div><div id="fans" class="fans"></div></section>
<form id="editor"><fieldset id="editable"><div class="layout"><section class="panel">
<div class="row between"><h2 style="margin:0">Recipe</h2><div class="row"><button type="button" id="new">New</button><button type="button" id="copy">Duplicate</button><button type="button" id="remove" class="danger">Delete</button></div></div>
<div class="field"><label for="profiles">Selected recipe</label><select id="profiles"></select></div><div class="field"><label for="name">Recipe name</label><input id="name" maxlength="40" required></div>
<svg viewBox="0 0 640 126" role="img" aria-label="Recipe target RPM over time"><line x1="18" y1="108" x2="622" y2="108" stroke="#344347"/><polyline id="curve" fill="none" stroke="#75e0c9" stroke-width="3" stroke-linejoin="round"/><text id="chart-top" x="18" y="16" fill="#a3b5b5" font-size="11"></text></svg>
<p id="duration" class="small"></p><table class="steps"><thead><tr><th>#</th><th>Target RPM</th><th>Slew (s)</th><th>Dwell (s)</th><th></th></tr></thead><tbody id="steps"></tbody></table>
<div class="row between"><button id="add-step" type="button">+ Add step</button><span class="small muted">2-9 steps. First and last targets stay at 0 RPM.</span></div>
<p class="small">Slew changes the RPM target over the set time. Dwell holds that target for the set time. Every fan follows the same schedule, even when its RPM is outside the warning band.</p>
</section><aside class="panel settings"><h2>Run settings</h2>
<div class="field"><label for="power">Power change limit <span class="unit">(% / s)</span></label><input id="power" type="number" min="0.1" max="100" step="0.1" required><small>Limits how quickly PWM power can change.</small></div>
<div class="field"><label for="tolerance">Seeker deadband <span class="unit">(RPM)</span></label><input id="tolerance" type="number" min="1" max="500" step="1" required><small>Power stays steady inside this RPM difference. Also sets the warning band at a zero RPM target.</small></div>
<div class="field"><label for="warning-percent">RPM warning threshold <span class="unit">(%)</span></label><input id="warning-percent" type="number" min="0.1" max="100" step="0.1" required><small>Allowed percentage difference from a nonzero RPM target before warning time starts.</small></div>
<div class="field"><label for="warning-delay">RPM warning delay <span class="unit">(s)</span></label><input id="warning-delay" type="number" min="0" max="120" step="0.1" required><small>Continuous time outside the band before turning red. Zero warns immediately; color clears on recovery. Power stays under speed control.</small></div>
<p class="small">These settings apply to every recipe. Configuration is locked while a run is active.</p></aside></div>
<div class="footer-actions"><div class="row"><button type="submit" class="primary" id="save">Save to coater</button><button type="button" id="import">Import JSON</button></div><span class="small muted">Motor start is available only on the coater.</span></div></fieldset></form>
<div class="footer-actions"><button type="button" id="export">Export JSON</button><span id="saved" class="small muted">Changes stay in this page until saved.</span></div><input type="file" id="file" accept="application/json,.json" hidden><div id="notice" role="status" aria-live="polite"></div>
</main><script>
'use strict';
const $=id=>document.getElementById(id), defaults={max_power_per_s:10,tolerance_rpm:50,rpm_warning_percent:5,rpm_warning_delay_s:2};
const settingLimits={max_power_per_s:[0.1,100],tolerance_rpm:[1,500],rpm_warning_percent:[0.1,100],rpm_warning_delay_s:[0,120]};
const legacyLimits={max_power_per_s:[0.1,100],tolerance_rpm:[1,500],settle_s:[0.05,10],reach_timeout_s:[1,120]};
const settingLabels={max_power_per_s:'Power change limit',tolerance_rpm:'Seeker deadband',rpm_warning_percent:'RPM warning threshold',rpm_warning_delay_s:'RPM warning delay',settle_s:'Legacy settle time',reach_timeout_s:'Legacy reach timeout'};
const settingFields=[['power','max_power_per_s'],['tolerance','tolerance_rpm'],['warning-percent','rpm_warning_percent'],['warning-delay','rpm_warning_delay_s']];
let cfg=null,running=false,dirty=false;
const clone=x=>JSON.parse(JSON.stringify(x));
const selected=()=>cfg.profiles.find(p=>p.name===cfg.selected);
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'error':'';}
function changed(){dirty=true;$('saved').textContent='Unsaved changes';preview();}
function validate(value){
 if(!value||value.version!==1||!Array.isArray(value.profiles)||value.profiles.length<1||value.profiles.length>20)throw Error('Use version 1 with one to twenty recipes.');
 const names=new Set();for(const p of value.profiles){if(typeof p.name!=='string'||!p.name.trim()||p.name.length>40||names.has(p.name))throw Error('Recipe names must be unique and 1-40 characters.');names.add(p.name);if(!Array.isArray(p.steps)||p.steps.length<2||p.steps.length>9)throw Error('Each recipe needs 2-9 steps.');for(const s of p.steps)for(const k of ['rpm','slew_s','dwell_s'])if(typeof s[k]!=='number'||!Number.isFinite(s[k])||s[k]<0)throw Error('Step values must be nonnegative numbers.');if(p.steps[0].rpm!==0||p.steps[p.steps.length-1].rpm!==0)throw Error('First and last targets must be 0 RPM.');}
 if(!names.has(value.selected))throw Error('Select an existing recipe.');
 const s=value.settings;
 if(!s||typeof s!=='object'||Array.isArray(s))throw Error('Include complete run settings.');
 const matches=limits=>Object.keys(s).length===Object.keys(limits).length&&Object.keys(limits).every(k=>Object.prototype.hasOwnProperty.call(s,k));
 const legacy=matches(legacyLimits),limits=legacy?legacyLimits:settingLimits;
 if(!legacy&&!matches(settingLimits))throw Error('Include a complete set of run settings.');
 for(const [key,[minimum,maximum]] of Object.entries(limits))if(typeof s[key]!=='number'||!Number.isFinite(s[key])||s[key]<minimum||s[key]>maximum)throw Error(settingLabels[key]+' must be between '+minimum+' and '+maximum+'.');
 if(legacy)value.settings={max_power_per_s:s.max_power_per_s,tolerance_rpm:s.tolerance_rpm,rpm_warning_percent:defaults.rpm_warning_percent,rpm_warning_delay_s:defaults.rpm_warning_delay_s};
 return value;
}
function preview(){if(!cfg)return;const steps=selected().steps;let total=0,previous=0,points=[[0,0]],maximum=1;for(const s of steps){total+=Number(s.slew_s)||0;points.push([total,Number(s.rpm)||0]);total+=Number(s.dwell_s)||0;points.push([total,Number(s.rpm)||0]);maximum=Math.max(maximum,Number(s.rpm)||0);}const data=points.map(p=>(18+604*p[0]/Math.max(total,1)).toFixed(1)+','+(108-80*p[1]/maximum).toFixed(1)).join(' ');$('curve').setAttribute('points',data);$('chart-top').textContent=Math.round(maximum)+' RPM';$('duration').textContent='Recipe duration: '+total.toFixed(1)+' s. RPM warnings do not pause the timeline.';}
function render(){const p=selected();$('profiles').replaceChildren();for(const item of cfg.profiles){const option=document.createElement('option');option.value=item.name;option.textContent=item.name;option.selected=item.name===cfg.selected;$('profiles').append(option);}$('name').value=p.name;$('steps').replaceChildren();p.steps.forEach((s,index)=>{const tr=document.createElement('tr'),n=document.createElement('td');n.textContent=index+1;tr.append(n);for(const key of ['rpm','slew_s','dwell_s']){const td=document.createElement('td'),input=document.createElement('input');input.type='number';input.min=0;input.max=key==='rpm'?4000:3600;input.step=key==='rpm'?'1':'0.1';input.required=true;input.value=s[key];input.setAttribute('aria-label','Step '+(index+1)+' '+key);input.disabled=key==='rpm'&&(index===0||index===p.steps.length-1);input.addEventListener('input',()=>{s[key]=Number(input.value);changed();});td.append(input);tr.append(td);}const td=document.createElement('td');if(index>0&&index<p.steps.length-1){const b=document.createElement('button');b.type='button';b.textContent='x';b.setAttribute('aria-label','Remove step '+(index+1));b.onclick=()=>{p.steps.splice(index,1);changed();render();};td.append(b);}tr.append(td);$('steps').append(tr);});for(const [id,key] of settingFields){$(id).value=cfg.settings[key];$(id).oninput=()=>{cfg.settings[key]=Number($(id).value);changed();};}$('add-step').disabled=p.steps.length>=9;$('remove').disabled=cfg.profiles.length<=1;$('new').disabled=$('copy').disabled=cfg.profiles.length>=20;preview();}
function uniqueName(base){let result=base,i=2;while(cfg.profiles.some(p=>p.name===result))result=base.slice(0,26)+' '+i++;return result;}
$('profiles').onchange=()=>{cfg.selected=$('profiles').value;changed();render();};
function commitName(){const p=selected(),name=$('name').value.trim();if(!name||name.length>40||cfg.profiles.some(q=>q!==p&&q.name===name)){notice('Use a unique recipe name of 1-40 characters.',true);return false;}if(p.name!==name){p.name=name;cfg.selected=name;changed();}$('name').value=name;return true;}
$('name').onchange=()=>{if(commitName())render();};
$('new').onclick=()=>{const name=uniqueName('New recipe');cfg.profiles.push({name,steps:[{rpm:0,slew_s:0,dwell_s:0},{rpm:3000,slew_s:15,dwell_s:30},{rpm:0,slew_s:15,dwell_s:0}]});cfg.selected=name;changed();render();};
$('copy').onclick=()=>{const p=clone(selected());p.name=uniqueName(p.name.slice(0,24)+' copy');cfg.profiles.push(p);cfg.selected=p.name;changed();render();};
$('remove').onclick=()=>{const name=cfg.selected;if(cfg.profiles.length>1){cfg.profiles=cfg.profiles.filter(p=>p.name!==name);cfg.selected=cfg.profiles[0].name;changed();render();}};
$('add-step').onclick=()=>{const p=selected();if(p.steps.length<9){p.steps.splice(p.steps.length-1,0,{rpm:3000,slew_s:15,dwell_s:30});changed();render();}};
async function request(path,options){const r=await fetch(path,options);let value;try{value=await r.json();}catch(e){throw Error('The coater returned an unreadable response.');}if(!r.ok)throw Error(value.error||'Request failed');return value;}
$('editor').onsubmit=async event=>{event.preventDefault();if(!cfg||running)return;try{if(!commitName())return;validate(cfg);$('save').disabled=true;cfg=validate(await request('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}));dirty=false;render();$('saved').textContent='Saved to coater';notice('Recipe and settings saved. Use the left START button at the bench.');}catch(e){notice(e.message,true);}finally{$('save').disabled=false;}};
$('export').onclick=()=>{if(!cfg)return;try{validate(cfg);const url=URL.createObjectURL(new Blob([JSON.stringify(cfg,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='gotham-recipes.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('Exported the recipes currently shown in this page.');}catch(e){notice(e.message,true);}};
$('import').onclick=()=>$('file').click();$('file').onchange=async()=>{const file=$('file').files[0];if(!file)return;try{if(file.size>32768)throw Error('Choose a JSON file smaller than 32 KiB.');cfg=validate(JSON.parse(await file.text()));changed();render();notice('Imported into the editor. Save to apply these recipes to the coater.');}catch(e){notice(e.message,true);}finally{$('file').value='';}};
function live(s){
 running=!!s.running;$('editable').disabled=running||!cfg;
 $('connection').textContent=running?'Run active':'Coater connected';
 const state=String(s.state||'IDLE').toUpperCase();
 $('run-state').textContent=state;$('run-state').style.color=state==='ERROR'?'var(--red)':'var(--accent)';
 $('target').textContent=Math.round(s.target_rpm||0)+' RPM';
 $('phase').textContent=running?'Step '+s.step+'/'+s.step_count+' | '+(s.phase_remaining_s==null?'--':Math.max(0,s.phase_remaining_s).toFixed(1)+' s remaining'):'Ready for a recipe';
 $('run-message').textContent=s.message||'Use the left START button on the coater.';
 $('fans').replaceChildren();
 for(let i=0;i<6;i++){
  const f=(s.fans||[])[i]||{},warning=!!(f.enabled&&f.warning),duty=f.enabled?Math.max(0,Math.min(100,f.duty||0)):0;
  const card=document.createElement('div'),label=document.createElement('span'),rpm=document.createElement('strong'),unit=document.createElement('span'),bar=document.createElement('div'),fill=document.createElement('i');
  card.className='fan'+(!f.enabled?' off':warning?' warning':'');
  label.className=unit.className='small muted';label.textContent='FAN #'+i;
  rpm.textContent=f.enabled&&f.valid?Math.round(f.rpm||0):'--';
  unit.textContent=f.enabled?'RPM | '+Math.round(duty)+'%':'Disabled';
  bar.className='bar';fill.style.width=duty+'%';bar.append(fill);card.append(label,rpm,unit,bar);
  if(warning){
   const detail=document.createElement('span');detail.className='rpm-warning';
   const error=Number.isFinite(f.error_percent)?Math.abs(f.error_percent).toFixed(1)+'% error':f.valid?'Off target':'No RPM reading';
   const duration=Number.isFinite(f.out_of_bounds_s)?' | '+Math.max(0,f.out_of_bounds_s).toFixed(1)+' s':'';
   detail.textContent='OFF TARGET: '+error+duration;card.append(detail);
  }
  if(f.enabled&&f.driver_error){const detail=document.createElement('span');detail.className='driver-error';detail.textContent='IO: '+String(f.driver_error).replace(/_/g,' ');card.append(detail);}
  $('fans').append(card);
 }
}
async function status(){try{live(await request('/api/status'));}catch(e){$('connection').textContent='Connection lost';}setTimeout(status,1000);}
(async()=>{try{cfg=validate(await request('/api/config'));render();notice('Ready. Start and stop runs using the physical buttons.');}catch(e){notice(e.message,true);$('editable').disabled=true;}status();})();
</script></body></html>'''
