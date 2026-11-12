// Disposable-runner only: real handler + real SQL RPCs; Auth and HTTP transport
// are substituted. This does not validate Supabase Auth, gateway or PostgREST.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHandler } from './handler.ts';

const [psql,port]=process.argv.slice(2);
assert.ok(psql && /^\d+$/.test(port), 'Run only through the disposable database runner');
const literal=value=>"'"+String(value).replaceAll("'","''")+"'";
function sql(statement,user='registry_test_owner') {
  return execFileSync(psql,['-X','-w','-h','127.0.0.1','-p',port,'-U',user,'-d','postgres',
    '-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose','-qAt','--single-transaction','-f','-'],
  {input:statement,encoding:'utf8',stdio:['pipe','pipe','pipe']}).trim();
}
const origin='https://registry.example.test';
const operations=new Set(['get_context','lookup_certificate','reserve_batch','list_batches',
  'get_batch_status','commit_batch','abandon_reservation']);
function fixture() {
  const owner=crypto.randomUUID(), other=crypto.randomUUID(), admin=crypto.randomUUID(), event=crypto.randomUUID();
  const generation=sql('SELECT generation FROM registry_private.registry_state;');
  sql(`UPDATE registry_private.registry_state SET mode='normal';
    INSERT INTO auth.users VALUES ('${owner}'),('${other}'),('${admin}');
    INSERT INTO registry_private.issuers(user_id,active,role)
    VALUES ('${owner}',true,'issuer'),('${other}',true,'issuer'),('${admin}',true,'registry_admin');
    INSERT INTO registry_private.events(id,event_key,internal_label,created_by)
    VALUES ('${event}','api-${event}','Synthetic API integration','${owner}');`);
  const tokens=new Map();
  for (const actor of [owner,other,admin]) {
    const encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url');
    tokens.set(actor,encode({alg:'HS256'})+'.'+encode({sub:actor,role:'authenticated',
      exp:Math.floor(Date.now()/1000)+600,iss:origin+'/auth/v1'})+'.synthetic-test-signature');
  }
  const handler=createHandler({url:origin,publishableKey:'synthetic-public',serviceKey:'synthetic-server',generation,enabled:true},async(url,init)=>{
    if(url.endsWith('/auth/v1/user')) {
      const actor=[...tokens].find(([,token])=>'Bearer '+token===init.headers.Authorization)?.[0];
      return actor?Response.json({id:actor,role:'authenticated',is_anonymous:false}):Response.json({}, {status:401});
    }
    const operation=url.split('/').at(-1); assert.ok(operations.has(operation));
    const args=JSON.parse(init.body);
    const parameters=Object.entries(args).map(([name,value])=>{
      assert.ok(['actor','generation','batch','query','request','manifest','page_limit','page_offset','filter_state'].includes(name));
      const type=['actor','generation','batch'].includes(name)?'uuid':
        ['query','request','manifest'].includes(name)?'jsonb':['page_limit','page_offset'].includes(name)?'integer':'text';
      return name+'=>'+(value===null?'NULL':literal(type==='jsonb'?JSON.stringify(value):value))+'::'+type;
    }).join(',');
    try {
      // Model PostgREST's authenticator -> service_role transition, without
      // replacing the real SECURITY DEFINER routine or its authorization.
      return Response.json(JSON.parse(sql(`SET LOCAL ROLE service_role; SELECT registry_api.${operation}(${parameters});`,'authenticator')));
    } catch(error) {
      const match=String(error.stderr).match(/ERROR:\s+([0-9A-Z]{5}):\s+([A-Z_]+)/);
      if(!match) throw error;
      return Response.json({code:match[1],message:match[2]},{status:400});
    }
  });
  async function request(actor,path,method='GET',body) {
    const response=await handler(new Request(origin+'/registry-v1'+path,{method,headers:{
      Authorization:'Bearer '+tokens.get(actor),'X-Registry-Generation':generation,'Content-Type':'application/json'},
    body:body===undefined?undefined:JSON.stringify(body)}));
    return {status:response.status,body:await response.json()};
  }
  const payload=(count=1)=>({event_id:event,formats:['jpg','pdf'],items:Array.from({length:count},(_,i)=>({
    client_item_id:crypto.randomUUID(),name:'Synthetic Participant',email:'person'+i+'@example.com',
    template_key:'synthetic',template_sha256:'ab'.repeat(32),render_profile:'v1'}))});
  return {owner,other,admin,event,request,payload};
}
function manifest(reservation) {
  return {version:1,intent_hash:reservation.intent_hash,artifacts:reservation.items.flatMap(item=>reservation.formats.map(format=>({
    item_id:item.item_id,certificate_id:item.certificate_id,code_id:item.code_id,format,
    sha256:'ab'.repeat(32),byte_count:100,filename:item.code+'.'+format})))};
}
function success(result) {assert.equal(result.status,200,JSON.stringify(result.body)); return result.body.data;}
function denied(result,status=403,code='FORBIDDEN') {
  assert.equal(result.status,status,JSON.stringify(result.body)); assert.equal(result.body.error.code,code);
  assert.doesNotMatch(JSON.stringify(result.body),/person0@|Synthetic Participant|template_sha256|synthetic-server/);
}

test('real SQL prevents reservation/abandon disclosure and permits closed-event owner/admin regeneration',async()=>{
  const f=fixture(), batch=crypto.randomUUID(), request=f.payload();
  const reserved=success(await f.request(f.owner,'/batches','POST',{batch_id:batch,request}));
  const issued=success(await f.request(f.owner,`/batches/${batch}/commit`,'POST',manifest(reserved)));
  const query={code:issued.items[0].code};
  denied(await f.request(f.other,'/certificates/lookup','POST',query));
  const attempted=crypto.randomUUID();
  denied(await f.request(f.other,'/batches','POST',{batch_id:attempted,request}));
  denied(await f.request(f.other,`/batches/${attempted}/abandon`,'POST',{}),404,'BATCH_NOT_FOUND');
  denied(await f.request(f.other,`/batches/${batch}/abandon`,'POST',{}));
  const own=f.payload(); own.items[0].email='other@example.com'; const ownBatch=crypto.randomUUID();
  success(await f.request(f.other,'/batches','POST',{batch_id:ownBatch,request:own}));
  success(await f.request(f.other,`/batches/${ownBatch}/abandon`,'POST',{}));
  denied(await f.request(f.other,'/certificates/lookup','POST',query));
  denied(await f.request(f.other,'/batches','POST',{batch_id:crypto.randomUUID(),request}));
  sql(`UPDATE registry_private.events SET state='closed' WHERE id='${f.event}';`);
  denied(await f.request(f.other,'/batches','POST',{batch_id:crypto.randomUUID(),request}));
  for(const actor of [f.owner,f.admin]) {
    assert.equal(success(await f.request(actor,'/certificates/lookup','POST',query)).found,true);
    const regeneration=crypto.randomUUID();
    const mapping=success(await f.request(actor,'/batches','POST',{batch_id:regeneration,request}));
    const result=success(await f.request(actor,`/batches/${regeneration}/commit`,'POST',manifest(mapping)));
    assert.deepEqual(result.issuance_rows,issued.issuance_rows);
  }
  const fresh=f.payload(); fresh.items[0].email='new@example.com';
  denied(await f.request(f.owner,'/batches','POST',{batch_id:crypto.randomUUID(),request:fresh}),409,'EVENT_UNAVAILABLE');
});

test('real handler and SQL agree on context, 500 both-format participants, 1000 artifacts and rejection of 501',async()=>{
  const f=fixture();
  assert.equal(success(await f.request(f.owner,'/context')).max_batch_items,500);
  const batch=crypto.randomUUID();
  const mapping=success(await f.request(f.owner,'/batches','POST',{batch_id:batch,request:f.payload(500)}));
  const artifacts=manifest(mapping); assert.equal(artifacts.artifacts.length,1000);
  assert.equal(success(await f.request(f.owner,`/batches/${batch}/commit`,'POST',artifacts)).issuance_rows.length,500);
  denied(await f.request(f.owner,'/batches','POST',{batch_id:crypto.randomUUID(),request:f.payload(501)}),400,'INVALID_REQUEST');
});
