import { createHandler } from './handler.ts';

// No credentials are read during tests; this entry point runs only in Edge runtime.
const handler=createHandler({
  url:Deno.env.get('SUPABASE_URL')??'',
  publishableKey:Deno.env.get('REGISTRY_PUBLISHABLE_KEY')??'',
  serviceKey:Deno.env.get('REGISTRY_SERVICE_KEY')??'',
  generation:Deno.env.get('REGISTRY_GENERATION')??'',
  enabled:Deno.env.get('REGISTRY_ENABLED')==='true',
});
Deno.serve(handler);
