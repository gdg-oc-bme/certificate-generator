-- ONLY for the disposable vanilla PostgreSQL cluster created by the test runner.
-- These are minimal Supabase contract fixtures, not a replacement for Supabase Auth.
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN BYPASSRLS;
CREATE ROLE authenticator LOGIN NOINHERIT;
GRANT anon, authenticated, service_role TO authenticator;
-- Match the relevant hosted postgres boundary, not the cluster superuser.
CREATE ROLE registry_migration LOGIN NOSUPERUSER CREATEROLE INHERIT NOBYPASSRLS;
GRANT CREATE ON DATABASE postgres TO registry_migration;
CREATE SCHEMA auth;
CREATE TABLE auth.users (id uuid PRIMARY KEY);
GRANT USAGE ON SCHEMA auth TO registry_migration;
GRANT REFERENCES ON auth.users TO registry_migration;
