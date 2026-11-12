import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHandler } from './handler.ts';

const actor = '00000000-0000-4000-8000-000000000001';
const other = '00000000-0000-4000-8000-000000000002';
const generation = '00000000-0000-4000-8000-000000000003';
const batch = '00000000-0000-4000-8000-000000000004';
const config = { url: 'https://registry.example.test', publishableKey: 'synthetic-public',
  serviceKey: 'synthetic-server', generation, enabled: true };
const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('synthetic-test-signing-material'),
  { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
async function jwt(overrides = {}) {
  const body = { sub: actor, role: 'authenticated', exp: Math.floor(Date.now()/1000)+600,
    iss: config.url+'/auth/v1', ...overrides };
  const unsigned = encode({alg:'HS256',typ:'JWT'})+'.'+encode(body);
  const sig = Buffer.from(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(unsigned))).toString('base64url');
  return unsigned+'.'+sig;
}
const issuedAt = '2026-11-09T10:30:00.123456Z';
function setup(options = {}) {
  const currentConfig = {...config,...options.config};
  const calls = [];
  let reserved, committed, abandoned = false;
  const network = async (url, init) => {
    calls.push({url, init});
    assert.equal(new URL(url).origin, config.url);
    assert.equal(init.redirect, 'error');
    if (url.endsWith('/auth/v1/user')) {
      assert.equal(init.headers.apikey, config.publishableKey);
      const token = init.headers.Authorization.slice(7);
      const [h,p,s] = token.split('.');
      const payload = JSON.parse(Buffer.from(p,'base64url'));
      const valid = await crypto.subtle.verify('HMAC', key, Buffer.from(s,'base64url'), new TextEncoder().encode(h+'.'+p));
      if (!valid || payload.exp <= Date.now()/1000) return Response.json({message:'invalid'}, {status:401});
      return Response.json({id:payload.sub,role:'authenticated',is_anonymous:!!options.anonymous});
    }
    assert.equal(init.headers.apikey, currentConfig.serviceKey);
    assert.equal(init.headers['Content-Profile'], 'registry_api');
    assert.equal(init.headers.Authorization, currentConfig.serviceKey.split('.').length===3?'Bearer '+currentConfig.serviceKey:undefined);
    const args = JSON.parse(init.body);
    assert.equal(args.actor, actor); // Every privileged call must use verified identity.
    assert.equal(args.generation, generation);
    if (options.disconnect) throw Error('connection details synthetic-private');
    const fail = options.error || (options.inactive ? 'ISSUER_DENIED' : options.recovery ? 'REGISTRY_RECOVERY' : undefined);
    if (fail) return Response.json({code:'P0001',message:fail,details:'alex@example.com synthetic-server SQL TRACE'}, {status:400});
    const operation = url.split('/').at(-1);
    if (operation === 'get_context') return Response.json({user_id:actor,role:options.admin?'registry_admin':'issuer',
      mode:'normal',registry_generation:generation,events:[{id:batch,event_key:'synthetic-event'}]});
    if (operation === 'lookup_certificate') return Response.json({found:true,code:'GDG-2026-0000',issued_at:issuedAt});
    if (operation === 'list_batches') return Response.json({batches:[],next_offset:null});
    const result = () => ({batch_id:batch,state:abandoned?'abandoned':committed?'completed':'reserved',
      issuance_rows:committed?[{code:'GDG-2026-0000',email:'alex@example.com',issued_at:issuedAt}]:[]});
    if (operation === 'reserve_batch') {
      if (reserved && JSON.stringify(reserved)!==JSON.stringify(args.request)) return Response.json({code:'P0001',message:'IDEMPOTENCY_CONFLICT'}, {status:400});
      reserved=args.request;
    } else if (operation === 'commit_batch') {
      if (committed && JSON.stringify(committed)!==JSON.stringify(args.manifest)) return Response.json({code:'P0001',message:'COMMIT_CONFLICT'}, {status:400});
      committed=args.manifest;
    } else if (operation === 'abandon_reservation') abandoned=true;
    return Response.json(result());
  };
  return {calls, handler:createHandler(currentConfig, network)};
}
async function request(handler, path='/context', method='GET', body, extra={}) {
  const headers = {Authorization:'Bearer '+await jwt(), 'X-Registry-Generation':generation, ...extra};
  if (body !== undefined) headers['Content-Type']='application/json';
  return handler(new Request(config.url+'/functions/v1/registry-v1'+path,
    {method,headers,body:body===undefined?undefined:typeof body==='string'?body:JSON.stringify(body)}));
}
const reservation = {batch_id:batch,request:{event_id:batch,formats:['pdf'],items:[{
  client_item_id:other,name:'Alex Example',email:'alex@example.com',template_key:'synthetic',
  template_sha256:'ab'.repeat(32),render_profile:'v1'}]}};
const manifest = {version:1,intent_hash:'ab'.repeat(32),artifacts:[{item_id:other,certificate_id:other,
  code_id:other,format:'pdf',sha256:'ab'.repeat(32),byte_count:100,filename:'GDG-2026-0000.pdf'}]};

test('valid JWT, context and event catalog',async()=>{
  const {handler}=setup(); const r=await request(handler); assert.equal(r.status,200);
  const d=await r.json(); assert.equal(d.data.user_id,actor); assert.equal(d.data.events.length,1);
});
for (const [name,header] of [['missing',undefined],['publishable only','Bearer sb_publishable_synthetic'],
  ['secret only','Bearer sb_secret_synthetic'],['malformed','Bearer invalid']]) {
  test('reject '+name,async()=>{
    const {handler,calls}=setup(); const headers = header?{Authorization:header}:{apikey:'synthetic-public'};
    const r=await handler(new Request(config.url+'/functions/v1/registry-v1/context',{headers}));
    assert.equal(r.status,401); assert.equal(calls.length,0);
  });
}
test('forged signature rejected by Auth boundary',async()=>{
  const {handler,calls}=setup(); const token=await jwt();
  const r=await request(handler,'/context','GET',undefined,{Authorization:'Bearer '+token.slice(0,-5)+'aaaaa'});
  assert.equal(r.status,401); assert.equal(calls.length,1);
});
test('expired JWT',async()=>{const {handler}=setup(); assert.equal((await request(handler,'/context','GET',undefined,{Authorization:'Bearer '+await jwt({exp:1})})).status,401);});
test('service role JWT',async()=>{const {handler}=setup(); assert.equal((await request(handler,'/context','GET',undefined,{Authorization:'Bearer '+await jwt({role:'service_role'})})).status,401);});
test('anonymous Auth user',async()=>{assert.equal((await request(setup({anonymous:true}).handler)).status,401);});
test('client actor elevation rejected',async()=>{assert.equal((await request(setup().handler,'/batches','POST',{...reservation,actor})).status,400);});
test('inactive issuer',async()=>{assert.equal((await request(setup({inactive:true}).handler)).status,403);});
test('cross issuer denial',async()=>{assert.equal((await request(setup({error:'BATCH_DENIED'}).handler,'/batches/'+batch)).status,403);});
test('recovery denial',async()=>{assert.equal((await request(setup({recovery:true}).handler)).status,503);});
test('certificate lookup',async()=>{assert.equal((await request(setup().handler,'/certificates/lookup','POST',{code:'GDG-2026-0000'})).status,200);});
test('reserve and identical replay',async()=>{const {handler}=setup(); for(let i=0;i<2;i++) assert.equal((await request(handler,'/batches','POST',reservation)).status,200);});
test('conflicting reservation',async()=>{const {handler}=setup(); await request(handler,'/batches','POST',reservation); const changed=structuredClone(reservation); changed.request.formats=['jpg']; assert.equal((await request(handler,'/batches','POST',changed)).status,409);});
test('pending export excluded',async()=>{const r=await request(setup().handler,'/batches/'+batch); assert.deepEqual((await r.json()).data.issuance_rows,[]);});
test('commit, replay and timestamp precision',async()=>{const {handler}=setup(); for(let i=0;i<2;i++) assert.equal((await request(handler,'/batches/'+batch+'/commit','POST',manifest)).status,200); const r=await request(handler,'/batches/'+batch); assert.equal((await r.json()).data.issuance_rows[0].issued_at,issuedAt);});
test('conflicting manifest',async()=>{const {handler}=setup(); await request(handler,'/batches/'+batch+'/commit','POST',manifest); const changed=structuredClone(manifest); changed.artifacts[0].byte_count=101; assert.equal((await request(handler,'/batches/'+batch+'/commit','POST',changed)).status,409);});
test('incomplete database manifest result',async()=>{assert.equal((await request(setup({error:'INCOMPLETE_MANIFEST'}).handler,'/batches/'+batch+'/commit','POST',manifest)).status,422);});
test('abandon repeated',async()=>{const {handler}=setup(); for(let i=0;i<2;i++) assert.equal((await request(handler,'/batches/'+batch+'/abandon','POST',{})).status,200);});
test('malformed JSON',async()=>{assert.equal((await request(setup().handler,'/batches','POST','{')).status,400);});
test('oversize actual streamed body',async()=>{assert.equal((await request(setup().handler,'/batches','POST',' '.repeat(2*1024*1024+1))).status,413);});
test('unsupported method',async()=>{assert.equal((await request(setup().handler,'/context','DELETE')).status,400);});
test('database failure redacted',async()=>{const r=await request(setup({disconnect:true}).handler); assert.equal(r.status,503); assert.doesNotMatch(await r.text(),/connection details|synthetic-private/);});
test('database details redacted',async()=>{const r=await request(setup({error:'unknown alex@example.com SQL'}).handler); assert.equal(r.status,503); assert.doesNotMatch(await r.text(),/alex@|synthetic-server|TRACE/);});
test('admin context',async()=>{const r=await request(setup({admin:true}).handler); assert.equal((await r.json()).data.role,'registry_admin');});
test('list pagination bounded',async()=>{assert.equal((await request(setup().handler,'/batches?limit=101')).status,400); assert.equal((await request(setup().handler,'/batches?limit=10&offset=0')).status,200);});
test('no unrestricted CORS',async()=>{const r=await request(setup().handler); assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);});
test('generation mismatch',async()=>{assert.equal((await request(setup().handler,'/batches/'+batch,'GET',undefined,{'X-Registry-Generation':other})).status,409);});
test('external gate defaults closed',async()=>{assert.equal((await request(setup({config:{enabled:false}}).handler)).status,503);});
test('origin requests denied',async()=>{assert.equal((await request(setup().handler,'/context','GET',undefined,{Origin:'https://example.com'})).status,403);});
test('query cannot supply actor',async()=>{assert.equal((await request(setup().handler,'/batches?actor='+other)).status,400);});
test('client timestamp rejected',async()=>{assert.equal((await request(setup().handler,'/batches/'+batch+'/commit','POST',{...manifest,issued_at:issuedAt})).status,400);});
test('allocation quota status',async()=>{assert.equal((await request(setup({error:'DAILY_LIMIT'}).handler,'/batches','POST',reservation)).status,429);});
test('unknown batch status',async()=>{assert.equal((await request(setup({error:'BATCH_NOT_FOUND'}).handler,'/batches/'+batch)).status,404);});
test('admin recovery commit',async()=>{assert.equal((await request(setup({admin:true}).handler,'/batches/'+batch+'/commit','POST',manifest)).status,200);});
test('fixed server URL',async()=>{assert.equal((await request(setup({config:{url:config.url+'?redirect=evil'}}).handler)).status,503);});
test('legacy server JWT transport stays server-side',async()=>{
  const serverKey=await jwt({role:'service_role'}); const {handler}=setup({config:{serviceKey:serverKey}});
  const r=await request(handler); assert.equal(r.status,200); assert.ok(!(await r.text()).includes(serverKey));
});
test('secret apikey without user bearer rejected',async()=>{
  const {handler,calls}=setup(); const r=await handler(new Request(config.url+'/functions/v1/registry-v1/context',{headers:{apikey:'synthetic-secret'}}));
  assert.equal(r.status,401); assert.equal(calls.length,0);
});
for (const [name,path,method,body] of [
  ['context','/context','GET',undefined], ['lookup','/certificates/lookup','POST',{code:'GDG-2026-0000'}],
  ['reserve','/batches','POST',reservation], ['list','/batches','GET',undefined],
  ['status','/batches/'+batch,'GET',undefined], ['commit','/batches/'+batch+'/commit','POST',manifest],
  ['abandon','/batches/'+batch+'/abandon','POST',{}],
]) {
  test('external/database mismatch blocks '+name,async()=>{
    const {handler,calls}=setup({error:'GENERATION_CHANGED'});
    const r=await request(handler,path,method,body); assert.equal(r.status,503);
    const value=await r.json(); assert.equal(value.error.code,'RECOVERY_REQUIRED');
    assert.doesNotMatch(JSON.stringify(value),new RegExp(generation+'|alex@|synthetic-server|TRACE'));
    const sent=JSON.parse(calls.at(-1).init.body); assert.equal(sent.generation,generation);
  });
}
for (const value of ['', 'not-a-uuid']) {
  test('external generation fails closed: '+JSON.stringify(value),async()=>{
    const {handler,calls}=setup({config:{generation:value}}); const r=await request(handler);
    assert.equal(r.status,503); assert.equal((await r.json()).error.code,'RECOVERY_REQUIRED'); assert.equal(calls.length,0);
  });
}
test('client cannot open maintenance or recovery controls',async()=>{
  const {handler}=setup(); const r=await request(handler,'/batches','POST',{...reservation,enabled:true,generation:other,mode:'normal'});
  assert.equal(r.status,400);
  assert.equal((await request(handler,'/recovery','POST',{})).status,400);
});

test('maintenance gate blocks before any Auth or RPC request',async()=>{
  const {handler,calls}=setup({config:{enabled:false}});
  const r=await request(handler,'/batches','POST',reservation);
  assert.equal(r.status,503); assert.equal((await r.json()).error.code,'REGISTRY_MAINTENANCE');
  assert.equal(calls.length,0);
});

test('unverified callers cannot reach a recovery-fenced RPC',async()=>{
  const {handler,calls}=setup({error:'GENERATION_CHANGED'});
  const r=await handler(new Request(config.url+'/functions/v1/registry-v1/context'));
  assert.equal(r.status,401); assert.equal(calls.length,0);
});

for (const formats of [['jpg'],['pdf'],['jpg','pdf']]) {
  test('500 participants accepted for '+formats.join('+'),async()=>{
    const body=structuredClone(reservation); body.request.formats=formats;
    body.request.items=Array.from({length:500},(_,i)=>({...reservation.request.items[0],client_item_id:crypto.randomUUID(),email:'person'+i+'@example.com'}));
    assert.equal((await request(setup().handler,'/batches','POST',body)).status,200);
    body.request.items.push({...body.request.items[0],client_item_id:crypto.randomUUID(),email:'person500@example.com'});
    const {handler,calls}=setup();
    assert.equal((await request(handler,'/batches','POST',body)).status,400);
    assert.equal(calls.filter(c=>c.url.includes('/rpc/')).length,0);
  });
}

test('1000 both-format artifacts accepted; 1001 rejected before RPC',async()=>{
  const body=structuredClone(manifest);
  body.artifacts=Array.from({length:500},()=>{
    const item_id=crypto.randomUUID();
    return ['jpg','pdf'].map(format=>({...manifest.artifacts[0],item_id,format,filename:'GDG-2026-0000.'+format}));
  }).flat();
  assert.equal((await request(setup().handler,'/batches/'+batch+'/commit','POST',body)).status,200);
  body.artifacts.push({...body.artifacts[0],item_id:crypto.randomUUID()});
  const {handler,calls}=setup();
  assert.equal((await request(handler,'/batches/'+batch+'/commit','POST',body)).status,400);
  assert.equal(calls.filter(c=>c.url.includes('/rpc/')).length,0);
});

for (const [path,body] of [['/certificates/lookup',{code:'GDG-2026-0000'}],['/batches',reservation]]) {
  test('certificate authorization denial is redacted for '+path,async()=>{
    const response=await request(setup({error:'CERTIFICATE_DENIED'}).handler,path,'POST',body);
    assert.equal(response.status,403);
    const data=await response.json(); assert.equal(data.error.code,'FORBIDDEN');
    assert.doesNotMatch(JSON.stringify(data),/alex@|Alex|synthetic-server|TRACE/);
  });
}
