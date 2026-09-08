"""Execute the offline editor JavaScript with a small DOM/fetch test double."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class WebpageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser-script checks')
    def test_recipe_edit_import_save_and_running_lock(self):
        path = Path(__file__).resolve().parents[1] / 'device' / 'webpage.py'
        spec = importlib.util.spec_from_file_location('webpage_test_data', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        script = module.PAGE.split(b'<script>', 1)[1].split(b'</script>', 1)[0].decode()
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
 $('name').value='Saved without blur';
 await $('editor').onsubmit({preventDefault(){}});
 assert.equal(puts.length,1);assert.equal(puts[0].profiles[1].steps.length,9);
 assert.equal(puts[0].selected,'Saved without blur');
 assert.equal(puts[0].profiles[1].steps[8].rpm,0);
 read("live({running:true,state:'DWELL',fans:[]})");
 assert.equal($('editable').disabled,true);
 await $('editor').onsubmit({preventDefault(){}});assert.equal(puts.length,1);
 read("live({running:false,state:'STOPPED',fans:[]})");
 const oldSelected=read('cfg.selected');
 $('file').files=[{size:7,text:async()=>'{broken'}];await $('file').onchange();
 assert.equal(read('cfg.selected'),oldSelected);assert.equal($('notice').className,'error');
 const imported=JSON.parse(JSON.stringify(documentData));imported.profiles[0].name='Imported';imported.selected='Imported';
 $('file').files=[{size:1000,text:async()=>JSON.stringify(imported)}];await $('file').onchange();
 assert.equal(read('cfg.selected'),'Imported');assert.equal(puts.length,1);
 assert.equal($('notice').className,'');
 $('export').onclick();assert.equal(exports.length,1);
 assert.deepEqual(JSON.parse(exports[0]),imported);
 console.log('Editor interactions passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run([shutil.which('node'), '-'], input=harness, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
