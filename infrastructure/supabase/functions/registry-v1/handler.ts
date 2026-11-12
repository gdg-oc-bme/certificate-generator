// Web-standard implementation, shared by the Deno entry point and offline tests.
export type Config = { url: string; publishableKey: string; serviceKey: string; generation: string; enabled: boolean };
type ObjectValue = Record<string, unknown>;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HEX = /^[0-9a-f]{64}$/i;
const MAX_BYTES = 2 * 1024 * 1024;
// Matches registry_state.max_batch_items's database ceiling. Both formats need
// one attestation each, so the largest supported manifest has 1000 artifacts.
const MAX_BATCH_ITEMS = 500;
const MAX_ARTIFACTS = MAX_BATCH_ITEMS * 2;
class ApiError extends Error {
  status: number;
  constructor(code: string, status = 400) { super(code); this.status = status; }
}
function object(value: unknown): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ApiError('INVALID_REQUEST');
  return value as ObjectValue;
}
function keys(value: ObjectValue, allowed: string[], required = allowed) {
  if (Object.keys(value).some(k => !allowed.includes(k)) || required.some(k => !(k in value))) throw new ApiError('INVALID_REQUEST');
}
function uuid(value: unknown): string {
  if (typeof value !== 'string' || !UUID.test(value)) throw new ApiError('INVALID_REQUEST');
  return value.toLowerCase();
}
function text(value: unknown, max = 1024): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max || /[\u0000-\u001f\u007f]/.test(value)) throw new ApiError('INVALID_REQUEST');
  return value;
}
function hex(value: unknown) {
  if (typeof value !== 'string' || !HEX.test(value)) throw new ApiError('INVALID_REQUEST');
}
function reserveRequest(body: ObjectValue) {
  keys(body, ['batch_id','request']); uuid(body.batch_id);
  const request = object(body.request); keys(request,['event_id','formats','items']); uuid(request.event_id);
  if (!Array.isArray(request.formats) || !request.formats.length || request.formats.length > 2 ||
      new Set(request.formats).size !== request.formats.length || request.formats.some(f => f !== 'jpg' && f !== 'pdf')) throw new ApiError('INVALID_REQUEST');
  if (!Array.isArray(request.items) || request.items.length < 1 || request.items.length > MAX_BATCH_ITEMS) throw new ApiError('INVALID_REQUEST');
  for (const raw of request.items) {
    const item = object(raw);
    keys(item,['client_item_id','name','email','template_key','template_sha256','render_profile','source_row','display_title','display_date'],
      ['client_item_id','name','email','template_key','template_sha256','render_profile']);
    uuid(item.client_item_id); text(item.name); text(item.email,254); text(item.template_key,128); hex(item.template_sha256); text(item.render_profile,128);
    if (item.source_row != null && (!Number.isSafeInteger(item.source_row) || Number(item.source_row) < 2)) throw new ApiError('INVALID_REQUEST');
    if (item.display_title !== undefined && (typeof item.display_title !== 'string' || item.display_title.length > 1024)) throw new ApiError('INVALID_REQUEST');
    if (item.display_date != null && (typeof item.display_date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(item.display_date) ||
        !Number.isFinite(Date.parse(item.display_date)) || new Date(item.display_date).toISOString().slice(0,10) !== item.display_date)) throw new ApiError('INVALID_REQUEST');
  }
}
function commitRequest(body: ObjectValue) {
  keys(body,['version','intent_hash','artifacts']);
  if (body.version !== 1) throw new ApiError('INVALID_REQUEST'); hex(body.intent_hash);
  if (!Array.isArray(body.artifacts) || !body.artifacts.length) throw new ApiError('INCOMPLETE_MANIFEST',422);
  if (body.artifacts.length > MAX_ARTIFACTS) throw new ApiError('INVALID_REQUEST');
  for (const raw of body.artifacts) {
    const a = object(raw); keys(a,['item_id','certificate_id','code_id','format','sha256','byte_count','filename']);
    uuid(a.item_id); uuid(a.certificate_id); uuid(a.code_id); hex(a.sha256);
    if (!['jpg','pdf'].includes(String(a.format)) || typeof a.format !== 'string' ||
        !Number.isSafeInteger(a.byte_count) || Number(a.byte_count) <= 0 || typeof a.filename !== 'string' ||
        !/^GDG-[0-9]{4}-[0-9]{4}\.(jpg|pdf)$/.test(a.filename)) throw new ApiError('INVALID_REQUEST');
  }
}
async function jsonBody(req: Request): Promise<ObjectValue> {
  if (req.headers.get('content-type')?.split(';')[0].trim() !== 'application/json' ||
      (req.headers.has('content-encoding') && req.headers.get('content-encoding') !== 'identity')) throw new ApiError('INVALID_REQUEST');
  const length = req.headers.get('content-length');
  if (length && (!/^\d+$/.test(length) || Number(length)>MAX_BYTES)) throw new ApiError('REQUEST_TOO_LARGE',413);
  if (!req.body) throw new ApiError('INVALID_REQUEST');
  const reader = req.body.getReader(); let size=0; const chunks: Uint8Array[]=[]; let expired=false;
  const timer=setTimeout(()=>{expired=true; void reader.cancel().catch(()=>{});},10000);
  try {
    while (true) {
      const {done,value}=await reader.read(); if(done) break;
      size+=value.byteLength; if(size>MAX_BYTES) { await reader.cancel(); throw new ApiError('REQUEST_TOO_LARGE',413); }
      chunks.push(value);
    }
  } finally { clearTimeout(timer); reader.releaseLock(); }
  if (expired) throw new ApiError('INVALID_REQUEST');
  const bytes=new Uint8Array(size); let offset=0;
  for (const chunk of chunks) {bytes.set(chunk,offset); offset+=chunk.length;}
  try {
    const parsed=object(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)));
    const stack: [unknown,number][]=[[parsed,0]];
    while(stack.length) {const [v,d]=stack.pop()!; if(d>12) throw new ApiError('INVALID_REQUEST');
      if(v && typeof v==='object') for(const child of Object.values(v)) stack.push([child,d+1]);}
    return parsed;
  } catch {throw new ApiError('INVALID_REQUEST');}
}
// Only exact allowlisted database messages become public domain errors.
function databaseError(error: unknown): ApiError {
  if (!error || typeof error !== 'object' || Array.isArray(error)) return new ApiError('SERVICE_UNAVAILABLE',503);
  const e=object(error); const message=typeof e.message==='string'?e.message:'';
  const map: Record<string,[string,number]>={
    ISSUER_DENIED:['FORBIDDEN',403], BATCH_DENIED:['FORBIDDEN',403], CERTIFICATE_DENIED:['FORBIDDEN',403],
    BATCH_NOT_FOUND:['BATCH_NOT_FOUND',404], IDEMPOTENCY_CONFLICT:['IDEMPOTENCY_CONFLICT',409],
    COMMIT_CONFLICT:['COMMIT_CONFLICT',409], CERTIFICATE_BUSY:['CERTIFICATE_BUSY',409], ISSUER_BUSY:['ISSUER_BUSY',409],
    YEAR_EXHAUSTED:['YEAR_EXHAUSTED',409], YEAR_ROLLOVER:['BATCH_EXPIRED_OR_YEAR_CHANGED',409],
    YEAR_ROLLOVER_RETRY:['BATCH_EXPIRED_OR_YEAR_CHANGED',409], BATCH_NOT_ISSUABLE:['BATCH_EXPIRED_OR_YEAR_CHANGED',409],
    BATCH_COMPLETED:['BATCH_COMPLETED',409], GENERATION_CHANGED:['RECOVERY_REQUIRED',503],
    REGISTRY_RECOVERY:['REGISTRY_RECOVERY_MODE',503], DAILY_LIMIT:['ALLOCATION_LIMIT',429],
    INCOMPLETE_MANIFEST:['INCOMPLETE_MANIFEST',422], EVENT_UNAVAILABLE:['EVENT_UNAVAILABLE',409],
    CONTENT_CONFLICT:['CONTENT_CONFLICT',409], RANDOMNESS_UNAVAILABLE:['SERVICE_UNAVAILABLE',503],
  };
  if (map[message]) return new ApiError(...map[message]);
  if (['INVALID_REQUEST','BATCH_LIMIT','INVALID_FORMATS','INVALID_PARTICIPANT','DUPLICATE_IDENTITY','DUPLICATE_ITEM',
    'INVALID_MANIFEST','INVALID_ARTIFACT','INVALID_MANIFEST_VALUE','ARTIFACT_ASSOCIATION','ARTIFACT_FILENAME','DUPLICATE_ARTIFACT'].includes(message)) return new ApiError('INVALID_REQUEST',422);
  if (typeof e.code==='string' && ['22P02','22003','22007','22008'].includes(e.code)) return new ApiError('INVALID_REQUEST',422);
  return new ApiError('SERVICE_UNAVAILABLE',503);
}
export function createHandler(config: Config, network: typeof fetch = fetch) {
  return async (req: Request): Promise<Response> => {
    const requestId=crypto.randomUUID();
    const respond=(data: unknown,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store','X-Request-ID':requestId,'X-Content-Type-Options':'nosniff'}});
    try {
      // The expected generation is trusted deployment configuration, never client input.
      // PostgreSQL compares it under the same lock as every protected operation.
      if (!UUID.test(config.generation)) throw new ApiError('RECOVERY_REQUIRED',503);
      if (!config.enabled) throw new ApiError('REGISTRY_MAINTENANCE',503);
      if (!config.publishableKey || !config.serviceKey ||
          !/^https:\/\/[^/]+$/.test(config.url) || new URL(config.url).origin!==config.url || new URL(config.url).username || new URL(config.url).password) throw new ApiError('SERVICE_UNAVAILABLE',503);
      if(req.headers.has('origin')) throw new ApiError('FORBIDDEN',403);
      const bearer=req.headers.get('authorization');
      if(!bearer || !/^Bearer [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(bearer) || bearer.length>8192) throw new ApiError('UNAUTHENTICATED',401);
      let claims: ObjectValue;
      try {claims=object(JSON.parse(atob(bearer.slice(7).split('.')[1].replace(/-/g,'+').replace(/_/g,'/'))));}
      catch {throw new ApiError('UNAUTHENTICATED',401);}
      // These checks only REJECT tokens. Identity is trusted only after Auth verifies it.
      if(claims.role!=='authenticated' || typeof claims.sub!=='string' || !UUID.test(claims.sub) ||
          typeof claims.exp!=='number' || !Number.isFinite(claims.exp) || claims.exp<=Date.now()/1000 || claims.iss!==config.url+'/auth/v1') throw new ApiError('UNAUTHENTICATED',401);
      const auth=await network(config.url+'/auth/v1/user',{headers:{apikey:config.publishableKey,Authorization:bearer},redirect:'error',signal:AbortSignal.timeout(5000)});
      if(auth.status===401 || auth.status===403) throw new ApiError('UNAUTHENTICATED',401);
      if(!auth.ok) throw new ApiError('SERVICE_UNAVAILABLE',503);
      const user=object(await auth.json());
      if(user.id!==claims.sub || user.role!=='authenticated' || user.is_anonymous===true) throw new ApiError('UNAUTHENTICATED',401);
      const actor=uuid(user.id);
      const url=new URL(req.url);
      const base=url.pathname.startsWith('/functions/v1/registry-v1')?'/functions/v1/registry-v1':'/registry-v1';
      if(!url.pathname.startsWith(base+'/')) throw new ApiError('INVALID_REQUEST');
      const path=url.pathname.slice(base.length);
      if(path!=='/context' && req.headers.get('x-registry-generation')?.toLowerCase()!==config.generation.toLowerCase()) throw new ApiError('GENERATION_CHANGED',409);
      const rpc=async(name: string,args: ObjectValue={})=>{
        const headers: Record<string,string>={apikey:config.serviceKey,
          'Content-Type':'application/json','Content-Profile':'registry_api','Accept-Profile':'registry_api'};
        // Legacy server keys are JWTs. New secret API keys belong only on apikey.
        if (/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(config.serviceKey)) headers.Authorization='Bearer '+config.serviceKey;
        const response=await network(config.url+'/rest/v1/rpc/'+name,{method:'POST',headers,
          body:JSON.stringify({...args,actor,generation:config.generation}),redirect:'error',signal:AbortSignal.timeout(15000)});
        if(!response.ok) {let error: unknown; try {error=await response.json();} catch {throw new ApiError('SERVICE_UNAVAILABLE',503);} throw databaseError(error);}
        const result=await response.json();
        if(result && result.error) throw databaseError({message:result.error});
        return result;
      };
      let result: unknown;
      if(path==='/context' && req.method==='GET') {
        if(url.search) throw new ApiError('INVALID_REQUEST'); result=await rpc('get_context');
      } else if(path==='/certificates/lookup' && req.method==='POST') {
        if(url.search) throw new ApiError('INVALID_REQUEST'); const query=await jsonBody(req);
        if('code' in query) {keys(query,['code']); if(typeof query.code!=='string'||!/^GDG-\d{4}-\d{4}$/.test(query.code)) throw new ApiError('INVALID_REQUEST');}
        else {keys(query,['event_id','email','certificate_type'],['event_id','email']); uuid(query.event_id); text(query.email,254); if(query.certificate_type!==undefined&&query.certificate_type!=='participation') throw new ApiError('INVALID_REQUEST');}
        result=await rpc('lookup_certificate',{query});
      } else if(path==='/batches' && req.method==='POST') {
        if(url.search) throw new ApiError('INVALID_REQUEST'); const body=await jsonBody(req); reserveRequest(body);
        result=await rpc('reserve_batch',{batch:uuid(body.batch_id),request:body.request});
      } else if(path==='/batches' && req.method==='GET') {
        if([...url.searchParams.keys()].some(k=>!['limit','offset','state'].includes(k)) || [...new Set(url.searchParams.keys())].some(k=>url.searchParams.getAll(k).length!==1)) throw new ApiError('INVALID_REQUEST');
        const rawLimit=url.searchParams.get('limit')??'25', rawOffset=url.searchParams.get('offset')??'0';
        const limit=Number(rawLimit), offset=Number(rawOffset), state=url.searchParams.get('state');
        if(!/^\d+$/.test(rawLimit)||!/^\d+$/.test(rawOffset)||!Number.isInteger(limit)||limit<1||limit>100||!Number.isInteger(offset)||offset>10000||
          (state!==null&&!['reserved','completed','abandoned','expired'].includes(state))) throw new ApiError('INVALID_REQUEST');
        result=await rpc('list_batches',{page_limit:limit,page_offset:offset,filter_state:state});
      } else {
        const match=path.match(/^\/batches\/([^/]+)(?:\/(commit|abandon))?$/);
        if(!match||url.search) throw new ApiError('INVALID_REQUEST'); const batch=uuid(match[1]);
        if(!match[2]&&req.method==='GET') result=await rpc('get_batch_status',{batch});
        else if(match[2]==='commit'&&req.method==='POST') {const manifest=await jsonBody(req); commitRequest(manifest); result=await rpc('commit_batch',{batch,manifest});}
        else if(match[2]==='abandon'&&req.method==='POST') {const body=await jsonBody(req); keys(body,[]); result=await rpc('abandon_reservation',{batch});}
        else throw new ApiError('INVALID_REQUEST');
      }
      return respond({data:result,request_id:requestId});
    } catch(error) {
      const safe=error instanceof ApiError?error:new ApiError('SERVICE_UNAVAILABLE',503);
      return respond({error:{code:safe.message},request_id:requestId},safe.status);
    }
  };
}
