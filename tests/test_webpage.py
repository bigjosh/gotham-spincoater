"""Execute the offline editor JavaScript with a small DOM/fetch test double."""
import importlib.util
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class WebpageTests(unittest.TestCase):
    def load_page(self):
        path = Path(__file__).resolve().parents[1] / 'device' / 'webpage.py'
        spec = importlib.util.spec_from_file_location('webpage_test_data', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PAGE

    def test_warning_form_bounds_match_zero_delay_and_fixed_timeline(self):
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.fields = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and 'id' in attrs:
                    self.fields[attrs['id']] = attrs

        page = self.load_page().decode()
        inputs = Inputs()
        inputs.feed(page)
        for name, minimum, maximum in (('power', '0.1', '100'),
                                        ('tolerance', '1', '500'),
                                        ('warning-percent', '0.1', '100'),
                                        ('warning-delay', '0', '120'),
                                        ('rpm-zero', '0', '1000')):
            self.assertEqual(inputs.fields[name]['type'], 'number')
            self.assertEqual(inputs.fields[name]['min'], minimum)
            self.assertEqual(inputs.fields[name]['max'], maximum)
        self.assertNotIn('settle', inputs.fields)
        self.assertNotIn('timeout', inputs.fields)
        self.assertIn('RPM warnings do not pause the timeline.', page)
        self.assertNotIn('FAULT', page)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser-script checks')
    def test_recipe_edit_import_save_and_running_lock(self):
        script = self.load_page().split(b'<script>', 1)[1].split(b'</script>', 1)[0].decode()
        harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(){this.children=[];this.value='';this.textContent='';this.style={};this.disabled=false;this.files=[];}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(k,v){this[k]=v;}
 addEventListener(k,fn){this['on'+k]=fn;}
 click(){if(this.onclick)this.onclick();}
}
const elements=new Map(),document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement(){return new Element();}};
let documentData={version:1,selected:'Default',settings:{max_power_per_s:10,tolerance_rpm:50,settle_s:0.5,reach_timeout_s:15},profiles:[{name:'Default',steps:[{rpm:0,slew_s:0,dwell_s:0},{rpm:3000,slew_s:15,dwell_s:30},{rpm:0,slew_s:15,dwell_s:0}]}]};
const puts=[],exports=[];
const context=vm.createContext({document,console,setTimeout:()=>0,Blob:class{constructor(parts){exports.push(parts.join(''));}},URL:{createObjectURL:()=>'',revokeObjectURL:()=>{}},fetch:async(path,options)=>{if(options){puts.push(JSON.parse(options.body));documentData=JSON.parse(options.body);}return{ok:true,json:async()=>path==='/api/status'?{running:false,state:'IDLE',fans:[]}:JSON.parse(JSON.stringify(documentData))};}});
vm.runInContext(SCRIPT,context);
const $=id=>document.getElementById(id),read=s=>vm.runInContext(s,context);
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(read('cfg.selected'),'Default');
 assert.deepEqual(JSON.parse(read('JSON.stringify(cfg.settings)')),{max_power_per_s:10,tolerance_rpm:50,rpm_warning_percent:5,rpm_warning_delay_s:2,rpm_zero_threshold:60});
 assert.equal($('warning-percent').value,5);assert.equal($('warning-delay').value,2);
 assert.equal($('rpm-zero').value,60);
 assert.equal($('steps').children.length,3);
 assert.equal($('steps').children[0].children[1].children[0].disabled,true);
 assert.equal($('steps').children[2].children[1].children[0].disabled,true);
 $('copy').onclick();assert.equal(read('cfg.profiles.length'),2);
 $('remove').onclick();assert.equal(read('cfg.profiles.length'),1);
 $('new').onclick();assert.equal(read('cfg.profiles.length'),2);
 $('name').value='R'.repeat(40);$('name').onchange();
 for(let i=0;i<12;i++)$('add-step').onclick();
 assert.equal(read('selected().steps.length'),9);
 assert.equal($('add-step').disabled,true);
 $('warning-percent').value='7.5';$('warning-percent').oninput();
 $('warning-delay').value='0';$('warning-delay').oninput();
 $('rpm-zero').value='45';$('rpm-zero').oninput();
 assert.equal(read('cfg.settings.rpm_warning_percent'),7.5);
 assert.equal(read('cfg.settings.rpm_warning_delay_s'),0);
 assert.equal(read('cfg.settings.rpm_zero_threshold'),45);
 for(const [key,values] of Object.entries({max_power_per_s:[0,101,true,'10'],tolerance_rpm:[0,501],rpm_warning_percent:[0,100.1,NaN,Infinity],rpm_warning_delay_s:[-0.1,120.1,null],rpm_zero_threshold:[-0.1,1000.1,true,'60',null,NaN,Infinity]})){
  for(const value of values){context.badSetting=value;assert.throws(()=>read("(()=>{const v=clone(cfg);v.settings["+JSON.stringify(key)+"]=badSetting;return validate(v)})()"));}
 }
 assert.throws(()=>read("(()=>{const v=clone(cfg);delete v.settings.rpm_warning_delay_s;return validate(v)})()"));
 assert.throws(()=>read("(()=>{const v=clone(cfg);v.settings.settle_s=0.5;return validate(v)})()"));
 for(const values of [[0.1,0],[100,120]]){context.boundaryValues=values;read("(()=>{const v=clone(cfg);v.settings.rpm_warning_percent=boundaryValues[0];v.settings.rpm_warning_delay_s=boundaryValues[1];return validate(v)})()");}
 for(const value of [0,1000]){context.boundaryZero=value;read("(()=>{const v=clone(cfg);v.settings.rpm_zero_threshold=boundaryZero;return validate(v)})()");}
 $('name').value='Saved without blur';
 await $('editor').onsubmit({preventDefault(){}});
 assert.equal(puts.length,1);assert.equal(puts[0].profiles[1].steps.length,9);
 assert.equal(puts[0].selected,'Saved without blur');
 assert.equal(puts[0].profiles[1].steps[8].rpm,0);
 assert.deepEqual(puts[0].settings,{max_power_per_s:10,tolerance_rpm:50,rpm_warning_percent:7.5,rpm_warning_delay_s:0,rpm_zero_threshold:45});
 read("live({running:true,state:'DWELL',fans:[]})");
 assert.equal($('editable').disabled,true);
 await $('editor').onsubmit({preventDefault(){}});assert.equal(puts.length,1);
 read("live({running:true,state:'DWELL',target_rpm:3000,fans:[{enabled:true,warning:false,in_bounds:false,out_of_bounds_s:1,valid:true,rpm:2200,duty:75}]})");
 assert.equal($('fans').children[0].className,'fan');
 read("live({running:true,state:'DWELL',target_rpm:3000,fans:[{enabled:true,warning:true,in_bounds:false,error_percent:26.7,out_of_bounds_s:2.1,valid:true,rpm:2200,duty:75},{enabled:true,warning:false,in_bounds:true,valid:true,rpm:2998,duty:69}]})");
 assert.equal($('editable').disabled,true);
 assert.equal($('run-state').textContent,'DWELL');
 assert.equal($('fans').children.length,6);
 const warning=$('fans').children[0],healthy=$('fans').children[1];
 assert.equal(warning.className,'fan warning');
 assert.equal(warning.children[1].textContent,2200);
 assert.equal(warning.children[2].textContent,'RPM | 75%');
 assert.equal(warning.children[3].children[0].style.width,'75%');
 assert.equal(warning.children[4].textContent,'OFF TARGET: 26.7% error | 2.1 s');
 assert.equal(healthy.className,'fan');
 assert.equal(healthy.children[1].textContent,2998);
 assert.equal(healthy.children[2].textContent,'RPM | 69%');
 assert.equal(healthy.children[3].children[0].style.width,'69%');
 assert.equal(healthy.children.length,4);
 assert.equal($('fans').children[2].className,'fan off');
 read("live({running:true,state:'DWELL',fans:[{enabled:true,warning:false,in_bounds:true,valid:true,rpm:2980,duty:74}]})");
 assert.equal($('fans').children[0].className,'fan');
 assert.equal($('fans').children[0].children.length,4);
 assert.equal($('fans').children[0].children[2].textContent,'RPM | 74%');
 assert.equal($('fans').children[0].children[3].children[0].style.width,'74%');
 assert.equal($('editable').disabled,true);
 read("live({running:true,state:'RAMP',fans:[{enabled:true,warning:true,valid:false,error_percent:null,out_of_bounds_s:3,duty:30},{enabled:false,warning:true,duty:80}]})");
 assert.equal($('fans').children[0].children[1].textContent,0);
 assert.equal($('fans').children[0].children[2].textContent,'RPM | 30%');
 assert.equal($('fans').children[0].children[4].textContent,'OFF TARGET: No RPM reading | 3.0 s');
 assert.equal($('fans').children[1].className,'fan off');
 assert.equal($('fans').children[1].children[2].textContent,'RPM | Disabled');
 assert.equal($('fans').children[1].children[3].children[0].style.width,'0%');
 read("live({running:true,state:'DWELL',fans:[{enabled:true,warning:false,driver_error:'command_underrun',valid:false,duty:0}]})");
 assert.equal($('fans').children[0].className,'fan');
 assert.equal($('fans').children[0].children[2].textContent,'RPM | 0%');
 assert.equal($('fans').children[0].children[4].className,'driver-error');
 assert.equal($('fans').children[0].children[4].textContent,'IO: command underrun');
 read("live({running:true,state:'DWELL',fans:[{enabled:true,warning:false,driver_error:'<img src=x onerror=alert(1)>',valid:false,duty:0}]})");
 assert.equal($('fans').children[0].children[4].textContent,'IO: <img src=x onerror=alert(1)>');
 read("live({running:false,state:'IDLE',fans:[{enabled:false,warning:false,driver_error:'tach monitor could not start',valid:false,display_rpm:0,duty:0}]})");
 assert.equal($('fans').children[0].className,'fan off');
 assert.equal($('fans').children[0].children[1].textContent,0);
 assert.equal($('fans').children[0].children[2].textContent,'RPM | Disabled');
 assert.equal($('fans').children[0].children[3].children[0].style.width,'0%');
 assert.equal($('fans').children[0].children[4].className,'driver-error');
 assert.equal($('fans').children[0].children[4].textContent,'IO: tach monitor could not start');
 read("live({running:false,state:'ERROR',message:'Controller error',fans:[{enabled:true,warning:false,duty:0}]})");
 assert.equal($('run-state').style.color,'var(--red)');
 assert.equal($('fans').children[0].className,'fan');
 assert.equal($('fans').children[0].children[2].textContent,'RPM | 0%');
 read("live({running:false,state:'STOPPED',fans:[]})");
 assert.equal($('run-state').style.color,'var(--accent)');
 for(const state of ['IDLE','STOPPED','COMPLETE']){
  context.coastState=state;
  read("live({running:false,state:coastState,fans:[{enabled:false,valid:true,rpm:1285,display_rpm:1285,duty:0},{enabled:true,valid:true,rpm:59.99,display_rpm:0,duty:0},{enabled:false,valid:true,rpm:60,display_rpm:60,duty:0},{enabled:false,valid:false,rpm:2000,display_rpm:0},{enabled:false,valid:true,rpm:61.2},{enabled:true,valid:false}]})");
  assert.deepEqual($('fans').children.map(card=>card.children[1].textContent),[1285,0,60,0,61,0]);
  assert.equal($('fans').children[0].children[2].textContent,'RPM | Disabled');
  assert.equal($('fans').children[0].children[3].children[0].style.width,'0%');
  assert.equal($('fans').children[3].children[4].textContent,'No recent tach');
  assert.equal($('fans').children[5].children[4].textContent,'No recent tach');
 }
 const oldSelected=read('cfg.selected');
 $('file').files=[{size:7,text:async()=>'{broken'}];await $('file').onchange();
 assert.equal(read('cfg.selected'),oldSelected);assert.equal($('notice').className,'error');
 const imported=JSON.parse(JSON.stringify(documentData));imported.profiles[0].name='Imported';imported.selected='Imported';
 imported.settings={max_power_per_s:12,tolerance_rpm:75,settle_s:0.8,reach_timeout_s:20};
 $('file').files=[{size:1000,text:async()=>JSON.stringify(imported)}];await $('file').onchange();
 assert.equal(read('cfg.selected'),'Imported');assert.equal(puts.length,1);
 assert.equal($('notice').className,'');
 assert.deepEqual(JSON.parse(read('JSON.stringify(cfg.profiles)')),imported.profiles);
 assert.deepEqual(JSON.parse(read('JSON.stringify(cfg.settings)')),{max_power_per_s:12,tolerance_rpm:75,rpm_warning_percent:5,rpm_warning_delay_s:2,rpm_zero_threshold:60});
 imported.settings={max_power_per_s:12,tolerance_rpm:75,rpm_warning_percent:5,rpm_warning_delay_s:2,rpm_zero_threshold:60};
 $('export').onclick();assert.equal(exports.length,1);
 assert.deepEqual(JSON.parse(exports[0]),imported);
 const previous=JSON.parse(JSON.stringify(imported));previous.settings={max_power_per_s:20,tolerance_rpm:90,rpm_warning_percent:8,rpm_warning_delay_s:0};
 $('file').files=[{size:1000,text:async()=>JSON.stringify(previous)}];await $('file').onchange();
 assert.deepEqual(JSON.parse(read('JSON.stringify(cfg.profiles)')),previous.profiles);
 assert.deepEqual(JSON.parse(read('JSON.stringify(cfg.settings)')),{max_power_per_s:20,tolerance_rpm:90,rpm_warning_percent:8,rpm_warning_delay_s:0,rpm_zero_threshold:60});
 assert.equal($('rpm-zero').value,60);
 const custom=JSON.parse(JSON.stringify(imported));custom.settings.rpm_warning_percent=9;custom.settings.rpm_warning_delay_s=0;custom.settings.rpm_zero_threshold=0;
 $('file').files=[{size:1000,text:async()=>JSON.stringify(custom)}];await $('file').onchange();
 assert.equal($('warning-percent').value,9);assert.equal($('warning-delay').value,0);
 assert.equal($('rpm-zero').value,0);
 $('export').onclick();assert.deepEqual(JSON.parse(exports[1]),custom);
 for(const legacy of [{max_power_per_s:10,tolerance_rpm:50,settle_s:0,reach_timeout_s:15},{max_power_per_s:10,tolerance_rpm:50,settle_s:0.5,reach_timeout_s:121},{max_power_per_s:10,tolerance_rpm:50,settle_s:0.5,reach_timeout_s:15,rpm_zero_threshold:60}]){
  const bad=JSON.parse(JSON.stringify(imported));bad.settings=legacy;
  $('file').files=[{size:1000,text:async()=>JSON.stringify(bad)}];await $('file').onchange();
  assert.equal($('notice').className,'error');assert.deepEqual(JSON.parse(read('JSON.stringify(cfg)')),custom);
 }
 console.log('Editor interactions passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run([shutil.which('node'), '-'], input=harness, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
