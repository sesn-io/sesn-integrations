import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const source = await readFile(new URL('../sesn-tautulli.mjs', import.meta.url), 'utf8');
const initial = () => ({tautulliUrl:'http://localhost:8181',tautulliKeyUrl:'http://localhost:8181',tautulliApiKey:'fixture-tautulli',notifierId:77,sesnKey:'fixture-sesn',sesnOrigin:'http://localhost',serverId:'fixture-server',notifierProof:{notifierId:'77',tautulliUrl:'http://localhost:8181',serverId:'fixture-server',hook:'http://localhost/api/v1/webhooks/tautulli',credentialHash:createHash('sha256').update('fixture-sesn').digest('hex')}});
async function run(config, reply, command = 'remove') {
  let saved=structuredClone(config), code=0; const calls=[], messages=[]; let newlyCreated=false;
  const names=['readFileSync','writeFileSync','existsSync','chmodSync','dirname','join','fileURLToPath','createInterface','process','console','fetch','createHash'];
  const args=[()=>JSON.stringify(saved),(_path,value)=>{saved=JSON.parse(value);},()=>true,()=>{},dirname,join,fileURLToPath,()=>{throw Error('unexpected prompt');},{argv:['node','script',command],env:{SESN_URL:'http://localhost',TAUTULLI_URL:'http://localhost:8181',TAUTULLI_API_KEY:'fixture-tautulli'},exit(n){const error=Error('exit');error.exitCode=n;throw error;}},{log:(...x)=>messages.push(x.join(' ')),error:(...x)=>messages.push(x.join(' ')),warn:(...x)=>messages.push(x.join(' '))},async(url,options)=>{const u=new URL(url);const call={path:u.pathname,cmd:u.searchParams.get('cmd'),url:String(url),options};calls.push(call);if(call.cmd==='get_server_identity')return ok({machine_identifier:'fixture-server'});if(call.cmd==='add_notifier_config')newlyCreated=true;if(call.cmd==='get_notifier_config')return ok(newlyCreated?{id:77,agent_id:25,config:{hook:'',method:'POST'},actions:{on_watched:0},notify_text:{on_watched:{subject:'',body:''}}}:{id:77,agent_id:25,config:{hook:'http://localhost/api/v1/webhooks/tautulli',method:'POST'},actions:{on_watched:1},notify_text:{on_watched:{subject:JSON.stringify({'X-Api-Key':'fixture-sesn'})}}});return reply(call,options);},createHash];
  const body=source.replace(/^#!.*\n/, '').replace(/^import .*;\n/gm,'').replaceAll('import.meta.url',JSON.stringify('file:///fixture/sesn-tautulli.mjs'));
  try { await new (Object.getPrototypeOf(async()=>{}).constructor)(...names,body)(...args); } catch(error){if(error.exitCode!==undefined)code=error.exitCode;else throw error;}
  assert.doesNotMatch(messages.join('\n'),/fixture-tautulli|fixture-sesn/);
  return {config:saved,code,calls,text:messages.join('\n')};
}
const ok=data=>Response.json({response:{result:'success',data}});
test('failed Sesn revocation retains a retry handle after notifier deletion',async()=>{
 const first=await run(initial(),c=>c.cmd?ok({}):Response.json({error:'unavailable'},{status:500}));
 assert.equal(first.code,1);assert.equal(first.config.sesnKey,'fixture-sesn');assert.equal(first.config.notifierId,undefined);assert.match(first.text,/incomplete|partial/i);
 const second=await run(first.config,c=>{assert.equal(c.cmd,null);return Response.json({ok:true});});
 assert.equal(second.code,0);assert.equal(second.config.sesnKey,undefined);assert.equal(second.calls.length,1);
});
test('failed notifier deletion still revokes Sesn and retains only local cleanup state',async()=>{
 const out=await run(initial(),c=>c.cmd==='delete_notifier'?Response.json({response:{result:'error'}},{status:503}):c.cmd==='get_notifiers'?ok([{notifier_id:77}]):Response.json({ok:true}));
 assert.equal(out.code,1);assert.equal(out.config.notifierId,77);assert.equal(out.config.tautulliApiKey,'fixture-tautulli');assert.equal(out.config.sesnKey,undefined);assert.ok(out.calls.some(c=>c.path==='/api/v1/link/revoke'));
});
test('already absent notifier and invalidated pairing are safely confirmed',async()=>{
 const out=await run(initial(),c=>c.cmd==='delete_notifier'?Response.json({response:{result:'error'}},{status:404}):c.cmd==='get_notifiers'?ok([]):Response.json({error:'invalid key'},{status:401}));
 assert.equal(out.code,0);assert.deepEqual(out.config,{tautulliUrl:'http://localhost:8181'});
});
test('malformed success or forbidden revoke response never erases a retry handle',async()=>{
 for(const response of [()=>Response.json({}),()=>Response.json({ok:false}),()=>Response.json({error:'scope'},{status:403})]){
  const out=await run(initial(),c=>c.cmd?ok({}):response());assert.equal(out.code,1);assert.equal(out.config.sesnKey,'fixture-sesn');
 }
});
test('local network failure cannot prevent independent Sesn cleanup',async()=>{
 const out=await run(initial(),c=>{if(c.cmd)throw Error('offline');return Response.json({ok:true});});
 assert.equal(out.code,1);assert.equal(out.config.notifierId,77);assert.equal(out.config.sesnKey,undefined);
});

test('setup and sync separate viewer discovery from playback filtering',async()=>{
 let tracked=['11'];
 const reply=(call)=>{
  if(call.cmd){
   const data=call.cmd==='get_server_identity'?{machine_identifier:'fixture-server'}
    :call.cmd==='get_server_info'?{pms_name:'Fixture server'}
    :call.cmd==='add_notifier_config'?{notifier_id:77}
    :call.cmd==='get_users'?[
      {user_id:11,friendly_name:'Tracked viewer',is_active:1},
      {user_id:22,username:'Untracked viewer',is_active:1},
      {user_id:0,username:'Local',is_active:1},
      {user_id:33,username:'Inactive',is_active:0},
    ]:{};
   return ok(data);
  }
  assert.equal(call.path,'/api/v1/connections/viewers');
  assert.equal(call.options.headers['x-api-key'],'fixture-sesn');
  assert.equal(call.options.redirect,'error');
  assert.doesNotMatch(call.options.body,/fixture-tautulli/);
  return Response.json({tracked});
 };
 const config=initial();delete config.notifierId;
 const first=await run(config,reply,'setup');
 assert.equal(first.code,0);
 assert.equal(first.config.notifierId,77);
 assert.equal(first.config.tautulliApiKey,'fixture-tautulli');
 const report=first.calls.find(c=>c.path==='/api/v1/connections/viewers');
 assert.deepEqual(JSON.parse(report.options.body).viewers,[{id:'11',name:'Tracked viewer'},{id:'22',name:'Untracked viewer'}]);
 const saved=first.calls.find(c=>c.cmd==='set_notifier_config');
 assert.equal(saved.options.method,'POST');
 assert.equal(saved.options.headers['X-Api-Key'],'fixture-tautulli');
 assert.doesNotMatch(saved.url,/fixture-tautulli/);
 const params=new URLSearchParams(saved.options.body);
 assert.deepEqual(JSON.parse(params.get('custom_conditions')),[
  {parameter:'user_id',operator:'is',value:['11'],type:'int'},
  {parameter:'media_type',operator:'is',value:['movie','episode'],type:'str'},
 ]);
 assert.equal(params.get('custom_conditions_logic'),'{1} and {2}');
 for(const trigger of ['on_play','on_pause','on_resume','on_stop','on_watched']){
  assert.equal(params.get(trigger),'1');
  assert.equal(JSON.parse(params.get(trigger+'_subject'))['X-Api-Key'],'fixture-sesn');
  const body=JSON.parse(params.get(trigger+'_body'));
  for(const [field,placeholder] of Object.entries({session_id:'session_id',rating_key:'rating_key',machine_id:'machine_id',started_at:'started_unixtime',stopped_at:'stopped_unixtime',unixtime:'unixtime'})){
   assert.equal(body[field],'{'+placeholder+'}');
  }
  assert.doesNotMatch(params.get(trigger+'_body'),/\{(?:ip_address|ip|location|file|file_path|row_id|reference_id)\}/);
 }
 tracked=[];
 const second=await run(first.config,reply,'sync');
 assert.equal(second.code,0);
 const synced=new URLSearchParams(second.calls.find(c=>c.cmd==='set_notifier_config').options.body);
 assert.deepEqual(JSON.parse(synced.get('custom_conditions'))[0].value,['-1']);
});
