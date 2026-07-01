--
-- PostgreSQL database dump
--

\restrict nRBbQvGZJy4alljx6HpiTBLhjCC4SWwzgNSprhX16CqJz9V4KHWQRrySPUN8DSB

-- Dumped from database version 17.6
-- Dumped by pg_dump version 18.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: auth; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA auth;


--
-- Name: extensions; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA extensions;


--
-- Name: graphql; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA graphql;


--
-- Name: graphql_public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA graphql_public;


--
-- Name: pgbouncer; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA pgbouncer;


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


--
-- Name: realtime; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA realtime;


--
-- Name: storage; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA storage;


--
-- Name: supabase_migrations; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA supabase_migrations;


--
-- Name: vault; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA vault;


--
-- Name: pg_stat_statements; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA extensions;


--
-- Name: EXTENSION pg_stat_statements; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_stat_statements IS 'track planning and execution statistics of all SQL statements executed';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: supabase_vault; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS supabase_vault WITH SCHEMA vault;


--
-- Name: EXTENSION supabase_vault; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION supabase_vault IS 'Supabase Vault Extension';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: aal_level; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.aal_level AS ENUM (
    'aal1',
    'aal2',
    'aal3'
);


--
-- Name: code_challenge_method; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.code_challenge_method AS ENUM (
    's256',
    'plain'
);


--
-- Name: factor_status; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.factor_status AS ENUM (
    'unverified',
    'verified'
);


--
-- Name: factor_type; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.factor_type AS ENUM (
    'totp',
    'webauthn',
    'phone'
);


--
-- Name: oauth_authorization_status; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.oauth_authorization_status AS ENUM (
    'pending',
    'approved',
    'denied',
    'expired'
);


--
-- Name: oauth_client_type; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.oauth_client_type AS ENUM (
    'public',
    'confidential'
);


--
-- Name: oauth_registration_type; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.oauth_registration_type AS ENUM (
    'dynamic',
    'manual'
);


--
-- Name: oauth_response_type; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.oauth_response_type AS ENUM (
    'code'
);


--
-- Name: one_time_token_type; Type: TYPE; Schema: auth; Owner: -
--

CREATE TYPE auth.one_time_token_type AS ENUM (
    'confirmation_token',
    'reauthentication_token',
    'recovery_token',
    'email_change_token_new',
    'email_change_token_current',
    'phone_change_token'
);


--
-- Name: asset_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.asset_type AS ENUM (
    'etf',
    'index',
    'crypto',
    'commodity'
);


--
-- Name: book_level; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.book_level AS ENUM (
    'Starter',
    'Intermediate',
    'Advanced'
);


--
-- Name: bookmark_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.bookmark_type AS ENUM (
    'book',
    'lesson',
    'article',
    'report'
);


--
-- Name: chat_message_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.chat_message_role AS ENUM (
    'user',
    'assistant',
    'system'
);


--
-- Name: lesson_level; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.lesson_level AS ENUM (
    'foundation',
    'analysis',
    'strategies',
    'mastery'
);


--
-- Name: lesson_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.lesson_status AS ENUM (
    'completed',
    'upNext',
    'notStarted'
);


--
-- Name: money_move_category; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.money_move_category AS ENUM (
    'blueprints',
    'valueTraps',
    'battles'
);


--
-- Name: news_sentiment; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.news_sentiment AS ENUM (
    'bullish',
    'bearish',
    'neutral'
);


--
-- Name: report_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.report_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed',
    'deleted'
);


--
-- Name: trade_action; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.trade_action AS ENUM (
    'BOUGHT',
    'SOLD'
);


--
-- Name: trade_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.trade_type AS ENUM (
    'New',
    'Increased',
    'Decreased',
    'Closed'
);


--
-- Name: user_tier; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.user_tier AS ENUM (
    'free',
    'pro',
    'premium'
);


--
-- Name: whale_category; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.whale_category AS ENUM (
    'investors',
    'institutions',
    'politicians',
    'crypto'
);


--
-- Name: whale_risk_profile; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.whale_risk_profile AS ENUM (
    'conservative',
    'moderate',
    'aggressive',
    'very_aggressive'
);


--
-- Name: action; Type: TYPE; Schema: realtime; Owner: -
--

CREATE TYPE realtime.action AS ENUM (
    'INSERT',
    'UPDATE',
    'DELETE',
    'TRUNCATE',
    'ERROR'
);


--
-- Name: equality_op; Type: TYPE; Schema: realtime; Owner: -
--

CREATE TYPE realtime.equality_op AS ENUM (
    'eq',
    'neq',
    'lt',
    'lte',
    'gt',
    'gte',
    'in',
    'like',
    'ilike',
    'is',
    'match',
    'imatch',
    'isdistinct'
);


--
-- Name: user_defined_filter; Type: TYPE; Schema: realtime; Owner: -
--

CREATE TYPE realtime.user_defined_filter AS (
	column_name text,
	op realtime.equality_op,
	value text
);


--
-- Name: wal_column; Type: TYPE; Schema: realtime; Owner: -
--

CREATE TYPE realtime.wal_column AS (
	name text,
	type_name text,
	type_oid oid,
	value jsonb,
	is_pkey boolean,
	is_selectable boolean
);


--
-- Name: wal_rls; Type: TYPE; Schema: realtime; Owner: -
--

CREATE TYPE realtime.wal_rls AS (
	wal jsonb,
	is_rls_enabled boolean,
	subscription_ids uuid[],
	errors text[]
);


--
-- Name: buckettype; Type: TYPE; Schema: storage; Owner: -
--

CREATE TYPE storage.buckettype AS ENUM (
    'STANDARD',
    'ANALYTICS',
    'VECTOR'
);


--
-- Name: email(); Type: FUNCTION; Schema: auth; Owner: -
--

CREATE FUNCTION auth.email() RETURNS text
    LANGUAGE sql STABLE
    AS $$
  select 
  coalesce(
    nullif(current_setting('request.jwt.claim.email', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'email')
  )::text
$$;


--
-- Name: FUNCTION email(); Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON FUNCTION auth.email() IS 'Deprecated. Use auth.jwt() -> ''email'' instead.';


--
-- Name: jwt(); Type: FUNCTION; Schema: auth; Owner: -
--

CREATE FUNCTION auth.jwt() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
  select 
    coalesce(
        nullif(current_setting('request.jwt.claim', true), ''),
        nullif(current_setting('request.jwt.claims', true), '')
    )::jsonb
$$;


--
-- Name: role(); Type: FUNCTION; Schema: auth; Owner: -
--

CREATE FUNCTION auth.role() RETURNS text
    LANGUAGE sql STABLE
    AS $$
  select 
  coalesce(
    nullif(current_setting('request.jwt.claim.role', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role')
  )::text
$$;


--
-- Name: FUNCTION role(); Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON FUNCTION auth.role() IS 'Deprecated. Use auth.jwt() -> ''role'' instead.';


--
-- Name: uid(); Type: FUNCTION; Schema: auth; Owner: -
--

CREATE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
  select 
  coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;


--
-- Name: FUNCTION uid(); Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON FUNCTION auth.uid() IS 'Deprecated. Use auth.jwt() -> ''sub'' instead.';


--
-- Name: grant_pg_cron_access(); Type: FUNCTION; Schema: extensions; Owner: -
--

CREATE FUNCTION extensions.grant_pg_cron_access() RETURNS event_trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF EXISTS (
    SELECT
    FROM pg_event_trigger_ddl_commands() AS ev
    JOIN pg_extension AS ext
    ON ev.objid = ext.oid
    WHERE ext.extname = 'pg_cron'
  )
  THEN
    grant usage on schema cron to postgres with grant option;

    alter default privileges in schema cron grant all on tables to postgres with grant option;
    alter default privileges in schema cron grant all on functions to postgres with grant option;
    alter default privileges in schema cron grant all on sequences to postgres with grant option;

    alter default privileges for user supabase_admin in schema cron grant all
        on sequences to postgres with grant option;
    alter default privileges for user supabase_admin in schema cron grant all
        on tables to postgres with grant option;
    alter default privileges for user supabase_admin in schema cron grant all
        on functions to postgres with grant option;

    grant all privileges on all tables in schema cron to postgres with grant option;
    revoke all on table cron.job from postgres;
    grant select on table cron.job to postgres with grant option;
  END IF;
END;
$$;


--
-- Name: FUNCTION grant_pg_cron_access(); Type: COMMENT; Schema: extensions; Owner: -
--

COMMENT ON FUNCTION extensions.grant_pg_cron_access() IS 'Grants access to pg_cron';


--
-- Name: grant_pg_graphql_access(); Type: FUNCTION; Schema: extensions; Owner: -
--

CREATE FUNCTION extensions.grant_pg_graphql_access() RETURNS event_trigger
    LANGUAGE plpgsql
    AS $_$
DECLARE
    func_is_graphql_resolve bool;
BEGIN
    func_is_graphql_resolve = (
        SELECT n.proname = 'resolve'
        FROM pg_event_trigger_ddl_commands() AS ev
        LEFT JOIN pg_catalog.pg_proc AS n
        ON ev.objid = n.oid
    );

    IF func_is_graphql_resolve
    THEN
        -- Update public wrapper to pass all arguments through to the pg_graphql resolve func
        DROP FUNCTION IF EXISTS graphql_public.graphql;
        create or replace function graphql_public.graphql(
            "operationName" text default null,
            query text default null,
            variables jsonb default null,
            extensions jsonb default null
        )
            returns jsonb
            language sql
        as $$
            select graphql.resolve(
                query := query,
                variables := coalesce(variables, '{}'),
                "operationName" := "operationName",
                extensions := extensions
            );
        $$;

        -- This hook executes when `graphql.resolve` is created. That is not necessarily the last
        -- function in the extension so we need to grant permissions on existing entities AND
        -- update default permissions to any others that are created after `graphql.resolve`
        grant usage on schema graphql to postgres, anon, authenticated, service_role;
        grant select on all tables in schema graphql to postgres, anon, authenticated, service_role;
        grant execute on all functions in schema graphql to postgres, anon, authenticated, service_role;
        grant all on all sequences in schema graphql to postgres, anon, authenticated, service_role;
        alter default privileges in schema graphql grant all on tables to postgres, anon, authenticated, service_role;
        alter default privileges in schema graphql grant all on functions to postgres, anon, authenticated, service_role;
        alter default privileges in schema graphql grant all on sequences to postgres, anon, authenticated, service_role;

        -- Allow postgres role to allow granting usage on graphql and graphql_public schemas to custom roles
        grant usage on schema graphql_public to postgres with grant option;
        grant usage on schema graphql to postgres with grant option;
    END IF;

END;
$_$;


--
-- Name: FUNCTION grant_pg_graphql_access(); Type: COMMENT; Schema: extensions; Owner: -
--

COMMENT ON FUNCTION extensions.grant_pg_graphql_access() IS 'Grants access to pg_graphql';


--
-- Name: grant_pg_net_access(); Type: FUNCTION; Schema: extensions; Owner: -
--

CREATE FUNCTION extensions.grant_pg_net_access() RETURNS event_trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_event_trigger_ddl_commands() AS ev
    JOIN pg_extension AS ext
    ON ev.objid = ext.oid
    WHERE ext.extname = 'pg_net'
  )
  THEN
    IF NOT EXISTS (
      SELECT 1
      FROM pg_roles
      WHERE rolname = 'supabase_functions_admin'
    )
    THEN
      CREATE USER supabase_functions_admin NOINHERIT CREATEROLE LOGIN NOREPLICATION;
    END IF;

    GRANT USAGE ON SCHEMA net TO supabase_functions_admin, postgres, anon, authenticated, service_role;

    IF EXISTS (
      SELECT FROM pg_extension
      WHERE extname = 'pg_net'
      -- all versions in use on existing projects as of 2025-02-20
      -- version 0.12.0 onwards don't need these applied
      AND extversion IN ('0.2', '0.6', '0.7', '0.7.1', '0.8', '0.10.0', '0.11.0')
    ) THEN
      ALTER function net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) SECURITY DEFINER;
      ALTER function net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) SECURITY DEFINER;

      ALTER function net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) SET search_path = net;
      ALTER function net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) SET search_path = net;

      REVOKE ALL ON FUNCTION net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) FROM PUBLIC;
      REVOKE ALL ON FUNCTION net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) FROM PUBLIC;

      GRANT EXECUTE ON FUNCTION net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) TO supabase_functions_admin, postgres, anon, authenticated, service_role;
      GRANT EXECUTE ON FUNCTION net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) TO supabase_functions_admin, postgres, anon, authenticated, service_role;
    END IF;
  END IF;
END;
$$;


--
-- Name: FUNCTION grant_pg_net_access(); Type: COMMENT; Schema: extensions; Owner: -
--

COMMENT ON FUNCTION extensions.grant_pg_net_access() IS 'Grants access to pg_net';


--
-- Name: pgrst_ddl_watch(); Type: FUNCTION; Schema: extensions; Owner: -
--

CREATE FUNCTION extensions.pgrst_ddl_watch() RETURNS event_trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands()
  LOOP
    IF cmd.command_tag IN (
      'CREATE SCHEMA', 'ALTER SCHEMA'
    , 'CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO', 'ALTER TABLE'
    , 'CREATE FOREIGN TABLE', 'ALTER FOREIGN TABLE'
    , 'CREATE VIEW', 'ALTER VIEW'
    , 'CREATE MATERIALIZED VIEW', 'ALTER MATERIALIZED VIEW'
    , 'CREATE FUNCTION', 'ALTER FUNCTION'
    , 'CREATE TRIGGER'
    , 'CREATE TYPE', 'ALTER TYPE'
    , 'CREATE RULE'
    , 'COMMENT'
    )
    -- don't notify in case of CREATE TEMP table or other objects created on pg_temp
    AND cmd.schema_name is distinct from 'pg_temp'
    THEN
      NOTIFY pgrst, 'reload schema';
    END IF;
  END LOOP;
END; $$;


--
-- Name: pgrst_drop_watch(); Type: FUNCTION; Schema: extensions; Owner: -
--

CREATE FUNCTION extensions.pgrst_drop_watch() RETURNS event_trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  obj record;
BEGIN
  FOR obj IN SELECT * FROM pg_event_trigger_dropped_objects()
  LOOP
    IF obj.object_type IN (
      'schema'
    , 'table'
    , 'foreign table'
    , 'view'
    , 'materialized view'
    , 'function'
    , 'trigger'
    , 'type'
    , 'rule'
    )
    AND obj.is_temporary IS false -- no pg_temp objects
    THEN
      NOTIFY pgrst, 'reload schema';
    END IF;
  END LOOP;
END; $$;


--
-- Name: set_graphql_placeholder(); Type: FUNCTION; Schema: extensions; Owner: -
--

CREATE FUNCTION extensions.set_graphql_placeholder() RETURNS event_trigger
    LANGUAGE plpgsql
    AS $_$
    DECLARE
    graphql_is_dropped bool;
    BEGIN
    graphql_is_dropped = (
        SELECT ev.schema_name = 'graphql_public'
        FROM pg_event_trigger_dropped_objects() AS ev
        WHERE ev.schema_name = 'graphql_public'
    );

    IF graphql_is_dropped
    THEN
        create or replace function graphql_public.graphql(
            "operationName" text default null,
            query text default null,
            variables jsonb default null,
            extensions jsonb default null
        )
            returns jsonb
            language plpgsql
        as $$
            DECLARE
                server_version float;
            BEGIN
                server_version = (SELECT (SPLIT_PART((select version()), ' ', 2))::float);

                IF server_version >= 14 THEN
                    RETURN jsonb_build_object(
                        'errors', jsonb_build_array(
                            jsonb_build_object(
                                'message', 'pg_graphql extension is not enabled.'
                            )
                        )
                    );
                ELSE
                    RETURN jsonb_build_object(
                        'errors', jsonb_build_array(
                            jsonb_build_object(
                                'message', 'pg_graphql is only available on projects running Postgres 14 onwards.'
                            )
                        )
                    );
                END IF;
            END;
        $$;
    END IF;

    END;
$_$;


--
-- Name: FUNCTION set_graphql_placeholder(); Type: COMMENT; Schema: extensions; Owner: -
--

COMMENT ON FUNCTION extensions.set_graphql_placeholder() IS 'Reintroduces placeholder function for graphql_public.graphql';


--
-- Name: graphql(text, text, jsonb, jsonb); Type: FUNCTION; Schema: graphql_public; Owner: -
--

CREATE FUNCTION graphql_public.graphql("operationName" text DEFAULT NULL::text, query text DEFAULT NULL::text, variables jsonb DEFAULT NULL::jsonb, extensions jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
            DECLARE
                server_version float;
            BEGIN
                server_version = (SELECT (SPLIT_PART((select version()), ' ', 2))::float);

                IF server_version >= 14 THEN
                    RETURN jsonb_build_object(
                        'errors', jsonb_build_array(
                            jsonb_build_object(
                                'message', 'pg_graphql extension is not enabled.'
                            )
                        )
                    );
                ELSE
                    RETURN jsonb_build_object(
                        'errors', jsonb_build_array(
                            jsonb_build_object(
                                'message', 'pg_graphql is only available on projects running Postgres 14 onwards.'
                            )
                        )
                    );
                END IF;
            END;
        $$;


--
-- Name: get_auth(text); Type: FUNCTION; Schema: pgbouncer; Owner: -
--

CREATE FUNCTION pgbouncer.get_auth(p_usename text) RETURNS TABLE(username text, password text)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO ''
    AS $_$
  BEGIN
      RAISE DEBUG 'PgBouncer auth request: %', p_usename;

      RETURN QUERY
      SELECT
          rolname::text,
          CASE WHEN rolvaliduntil < now()
              THEN null
              ELSE rolpassword::text
          END
      FROM pg_authid
      WHERE rolname=$1 and rolcanlogin;
  END;
  $_$;


--
-- Name: charge_user_credits(uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.charge_user_credits(p_user_id uuid, p_amount integer) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    new_remaining INT;
BEGIN
    UPDATE public.user_credits
       SET used       = used + p_amount,
           updated_at = now()
     WHERE user_id   = p_user_id
       AND (total - used) >= p_amount
    RETURNING (total - used) INTO new_remaining;

    RETURN new_remaining;  -- NULL when WHERE missed (insufficient balance)
END;
$$;


--
-- Name: cleanup_expired_news_cache(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_expired_news_cache() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    DELETE FROM ticker_news_cache WHERE expires_at < now();
END;
$$;


--
-- Name: cleanup_old_social_mentions(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_old_social_mentions() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    DELETE FROM social_mentions_history
    WHERE snapshot_date < CURRENT_DATE - INTERVAL '30 days';
END;
$$;


--
-- Name: create_user_credits(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_user_credits() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    INSERT INTO user_credits (user_id, total, used)
    VALUES (NEW.id, CASE NEW.tier
        WHEN 'free' THEN 3
        WHEN 'pro' THEN 25
        WHEN 'premium' THEN 100
    END, 0);
    RETURN NEW;
END;
$$;


--
-- Name: get_top_watchlist_tickers(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_top_watchlist_tickers(n integer DEFAULT 20) RETURNS TABLE(ticker text, watch_count bigint)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    RETURN QUERY
    SELECT wi.ticker, COUNT(*) as watch_count
    FROM watchlist_items wi
    GROUP BY wi.ticker
    ORDER BY watch_count DESC
    LIMIT n;
END;
$$;


--
-- Name: handle_new_auth_user(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.handle_new_auth_user() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    SET row_security TO 'off'
    AS $$
BEGIN
    INSERT INTO public.users (id, email, display_name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(
            NEW.raw_user_meta_data->>'display_name',
            NEW.raw_user_meta_data->>'full_name',
            split_part(NEW.email, '@', 1)
        ),
        NEW.raw_user_meta_data->>'avatar_url'
    )
    ON CONFLICT (id) DO NOTHING;

    -- Seed credits so the first /research/generate has a row to charge.
    -- 50 credits = 10 deep-research runs at 5 credits each.
    INSERT INTO public.user_credits (user_id, total, used)
    VALUES (NEW.id, 50, 0)
    ON CONFLICT (user_id) DO NOTHING;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    -- Mirror failure must never block the auth.users insert. Log it
    -- (visible in Postgres logs) and let the auth row through. A
    -- backfill script can sync any users that landed here.
    RAISE WARNING 'handle_new_auth_user failed for user % (%): %',
        NEW.id, NEW.email, SQLERRM;
    RETURN NEW;
END;
$$;


--
-- Name: increment_chat_message_count(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.increment_chat_message_count() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE chat_sessions
    SET message_count = message_count + 1,
        last_message_at = now()
    WHERE id = NEW.session_id;
    RETURN NEW;
END;
$$;


--
-- Name: refund_user_credits(uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refund_user_credits(p_user_id uuid, p_amount integer) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    new_remaining INT;
BEGIN
    UPDATE public.user_credits
       SET used       = GREATEST(0, used - p_amount),
           updated_at = now()
     WHERE user_id   = p_user_id
    RETURNING (total - used) INTO new_remaining;

    RETURN new_remaining;
END;
$$;


--
-- Name: search_all_chunks(public.vector, double precision, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_all_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 10) RETURNS TABLE(source_type text, source_id uuid, source_label text, section_title text, chunk_text text, similarity double precision)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    RETURN QUERY
    (
        SELECT
            'book'::TEXT AS source_type,
            bc.book_id AS source_id,
            b.title || ' by ' || b.author AS source_label,
            bc.section_title,
            bc.chunk_text,
            (1 - (bc.embedding <=> query_embedding))::FLOAT AS similarity
        FROM book_chunks bc
        JOIN books b ON bc.book_id = b.id
        WHERE (1 - (bc.embedding <=> query_embedding)) > match_threshold
    )
    UNION ALL
    (
        SELECT
            'article'::TEXT,
            ac.article_id,
            'Article' AS source_label,
            ac.section_title,
            ac.chunk_text,
            (1 - (ac.embedding <=> query_embedding))::FLOAT
        FROM article_chunks ac
        WHERE (1 - (ac.embedding <=> query_embedding)) > match_threshold
    )
    UNION ALL
    (
        SELECT
            'filing'::TEXT,
            cfc.id,
            cfc.ticker || ' ' || cfc.filing_type || ' ' || cfc.fiscal_year::TEXT AS source_label,
            cfc.section_title,
            cfc.chunk_text,
            (1 - (cfc.embedding <=> query_embedding))::FLOAT
        FROM company_filing_chunks cfc
        WHERE (1 - (cfc.embedding <=> query_embedding)) > match_threshold
    )
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;


--
-- Name: FUNCTION search_all_chunks(query_embedding public.vector, match_threshold double precision, match_count integer); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.search_all_chunks(query_embedding public.vector, match_threshold double precision, match_count integer) IS 'Cross-source RAG search across books, articles, and SEC filings.';


--
-- Name: search_article_chunks(public.vector, double precision, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_article_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 5) RETURNS TABLE(id uuid, article_id uuid, section_title text, chunk_text text, similarity double precision)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        ac.id,
        ac.article_id,
        ac.section_title,
        ac.chunk_text,
        (1 - (ac.embedding <=> query_embedding))::FLOAT AS similarity
    FROM article_chunks ac
    WHERE (1 - (ac.embedding <=> query_embedding)) > match_threshold
    ORDER BY ac.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


--
-- Name: search_book_chunks(public.vector, double precision, integer, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_book_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 5, filter_book_id uuid DEFAULT NULL::uuid) RETURNS TABLE(id uuid, book_id uuid, book_title text, book_author text, chapter_number integer, section_title text, chunk_text text, similarity double precision)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        bc.id,
        bc.book_id,
        b.title AS book_title,
        b.author AS book_author,
        bc.chapter_number,
        bc.section_title,
        bc.chunk_text,
        (1 - (bc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM book_chunks bc
    JOIN books b ON bc.book_id = b.id
    WHERE (1 - (bc.embedding <=> query_embedding)) > match_threshold
      AND (filter_book_id IS NULL OR bc.book_id = filter_book_id)
    ORDER BY bc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


--
-- Name: FUNCTION search_book_chunks(query_embedding public.vector, match_threshold double precision, match_count integer, filter_book_id uuid); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.search_book_chunks(query_embedding public.vector, match_threshold double precision, match_count integer, filter_book_id uuid) IS 'Semantic search across book content. Returns top matches above similarity threshold.';


--
-- Name: search_filing_chunks(public.vector, double precision, integer, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_filing_chunks(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 5, filter_ticker text DEFAULT NULL::text, filter_filing_type text DEFAULT NULL::text) RETURNS TABLE(id uuid, ticker text, filing_type text, fiscal_year integer, fiscal_quarter integer, section_title text, chunk_text text, similarity double precision)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        cfc.id,
        cfc.ticker,
        cfc.filing_type,
        cfc.fiscal_year,
        cfc.fiscal_quarter,
        cfc.section_title,
        cfc.chunk_text,
        (1 - (cfc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM company_filing_chunks cfc
    WHERE (1 - (cfc.embedding <=> query_embedding)) > match_threshold
      AND (filter_ticker IS NULL OR cfc.ticker = filter_ticker)
      AND (filter_filing_type IS NULL OR cfc.filing_type = filter_filing_type)
    ORDER BY cfc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


--
-- Name: FUNCTION search_filing_chunks(query_embedding public.vector, match_threshold double precision, match_count integer, filter_ticker text, filter_filing_type text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.search_filing_chunks(query_embedding public.vector, match_threshold double precision, match_count integer, filter_ticker text, filter_filing_type text) IS 'Semantic search across SEC filing chunks. Optionally filter by ticker and filing type.';


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: update_whale_followers_count(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_whale_followers_count() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE whales SET followers_count = followers_count + 1
        WHERE id = NEW.whale_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE whales SET followers_count = followers_count - 1
        WHERE id = OLD.whale_id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: apply_rls(jsonb, integer); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.apply_rls(wal jsonb, max_record_bytes integer DEFAULT (1024 * 1024)) RETURNS SETOF realtime.wal_rls
    LANGUAGE plpgsql
    AS $$
declare
    -- Regclass of the table e.g. public.notes
    entity_ regclass = (quote_ident(wal ->> 'schema') || '.' || quote_ident(wal ->> 'table'))::regclass;

    -- I, U, D, T: insert, update ...
    action realtime.action = (
        case wal ->> 'action'
            when 'I' then 'INSERT'
            when 'U' then 'UPDATE'
            when 'D' then 'DELETE'
            else 'ERROR'
        end
    );

    -- Is row level security enabled for the table
    is_rls_enabled bool = relrowsecurity from pg_class where oid = entity_;

    subscriptions realtime.subscription[] = array_agg(subs)
        from
            realtime.subscription subs
        where
            subs.entity = entity_
            -- Filter by action early - only get subscriptions interested in this action
            -- action_filter column can be: '*' (all), 'INSERT', 'UPDATE', or 'DELETE'
            and (subs.action_filter = '*' or subs.action_filter = action::text);

    -- Subscription vars
    working_role regrole;
    working_selected_columns text[];
    claimed_role regrole;
    claims jsonb;

    subscription_id uuid;
    subscription_has_access bool;
    visible_to_subscription_ids uuid[] = '{}';

    -- structured info for wal's columns
    columns realtime.wal_column[];
    -- previous identity values for update/delete
    old_columns realtime.wal_column[];

    error_record_exceeds_max_size boolean = octet_length(wal::text) > max_record_bytes;

    -- Primary jsonb output for record
    output jsonb;

    -- Loop record for iterating unique roles (outer loop)
    role_record record;
    -- Loop record for iterating unique selected_columns within a role (inner loop)
    cols_record record;
    -- Subscription ids visible at the role level (before fanning out by selected_columns)
    visible_role_sub_ids uuid[] = '{}';

begin
    perform set_config('role', null, true);

    columns =
        array_agg(
            (
                x->>'name',
                x->>'type',
                x->>'typeoid',
                realtime.cast(
                    (x->'value') #>> '{}',
                    coalesce(
                        (x->>'typeoid')::regtype, -- null when wal2json version <= 2.4
                        (x->>'type')::regtype
                    )
                ),
                (pks ->> 'name') is not null,
                true
            )::realtime.wal_column
        )
        from
            jsonb_array_elements(wal -> 'columns') x
            left join jsonb_array_elements(wal -> 'pk') pks
                on (x ->> 'name') = (pks ->> 'name');

    old_columns =
        array_agg(
            (
                x->>'name',
                x->>'type',
                x->>'typeoid',
                realtime.cast(
                    (x->'value') #>> '{}',
                    coalesce(
                        (x->>'typeoid')::regtype, -- null when wal2json version <= 2.4
                        (x->>'type')::regtype
                    )
                ),
                (pks ->> 'name') is not null,
                true
            )::realtime.wal_column
        )
        from
            jsonb_array_elements(wal -> 'identity') x
            left join jsonb_array_elements(wal -> 'pk') pks
                on (x ->> 'name') = (pks ->> 'name');

    for role_record in
        select claims_role
        from (select distinct claims_role from unnest(subscriptions)) t
        order by claims_role::text
    loop
        working_role := role_record.claims_role;

        -- Update `is_selectable` for columns and old_columns (once per role)
        columns =
            array_agg(
                (
                    c.name,
                    c.type_name,
                    c.type_oid,
                    c.value,
                    c.is_pkey,
                    pg_catalog.has_column_privilege(working_role, entity_, c.name, 'SELECT')
                )::realtime.wal_column
            )
            from
                unnest(columns) c;

        old_columns =
                array_agg(
                    (
                        c.name,
                        c.type_name,
                        c.type_oid,
                        c.value,
                        c.is_pkey,
                        pg_catalog.has_column_privilege(working_role, entity_, c.name, 'SELECT')
                    )::realtime.wal_column
                )
                from
                    unnest(old_columns) c;

        if action <> 'DELETE' and count(1) = 0 from unnest(columns) c where c.is_pkey then
            -- Fan out 400 error per distinct selected_columns for this role
            for cols_record in
                select selected_columns
                from (select distinct selected_columns from unnest(subscriptions) s where s.claims_role = working_role) t
                order by coalesce(array_to_string(selected_columns, ','), '')
            loop
                working_selected_columns := cols_record.selected_columns;
                return next (
                    jsonb_build_object(
                        'schema', wal ->> 'schema',
                        'table', wal ->> 'table',
                        'type', action
                    ),
                    is_rls_enabled,
                    (select array_agg(s.subscription_id) from unnest(subscriptions) as s where s.claims_role = working_role and (s.selected_columns is not distinct from working_selected_columns)),
                    array['Error 400: Bad Request, no primary key']
                )::realtime.wal_rls;
            end loop;

        -- The claims role does not have SELECT permission to the primary key of entity
        elsif action <> 'DELETE' and sum(c.is_selectable::int) <> count(1) from unnest(columns) c where c.is_pkey then
            -- Fan out 401 error per distinct selected_columns for this role
            for cols_record in
                select selected_columns
                from (select distinct selected_columns from unnest(subscriptions) s where s.claims_role = working_role) t
                order by coalesce(array_to_string(selected_columns, ','), '')
            loop
                working_selected_columns := cols_record.selected_columns;
                return next (
                    jsonb_build_object(
                        'schema', wal ->> 'schema',
                        'table', wal ->> 'table',
                        'type', action
                    ),
                    is_rls_enabled,
                    (select array_agg(s.subscription_id) from unnest(subscriptions) as s where s.claims_role = working_role and (s.selected_columns is not distinct from working_selected_columns)),
                    array['Error 401: Unauthorized']
                )::realtime.wal_rls;
            end loop;

        else
            -- Create the prepared statement (once per role)
            if is_rls_enabled and action <> 'DELETE' then
                if (select 1 from pg_prepared_statements where name = 'walrus_rls_stmt' limit 1) > 0 then
                    deallocate walrus_rls_stmt;
                end if;
                execute realtime.build_prepared_statement_sql('walrus_rls_stmt', entity_, columns);
            end if;

            -- Collect all visible subscription IDs for this role (filter check + RLS check)
            visible_role_sub_ids = '{}';

            for subscription_id, claims in (
                    select
                        subs.subscription_id,
                        subs.claims
                    from
                        unnest(subscriptions) subs
                    where
                        subs.entity = entity_
                        and subs.claims_role = working_role
                        and (
                            realtime.is_visible_through_filters(columns, subs.filters)
                            or (
                              action = 'DELETE'
                              and realtime.is_visible_through_filters(old_columns, subs.filters)
                            )
                        )
            ) loop

                if not is_rls_enabled or action = 'DELETE' then
                    visible_role_sub_ids = visible_role_sub_ids || subscription_id;
                else
                    -- Check if RLS allows the role to see the record
                    perform
                        -- Trim leading and trailing quotes from working_role because set_config
                        -- doesn't recognize the role as valid if they are included
                        set_config('role', trim(both '"' from working_role::text), true),
                        set_config('request.jwt.claims', claims::text, true);

                    execute 'execute walrus_rls_stmt' into subscription_has_access;

                    if subscription_has_access then
                        visible_role_sub_ids = visible_role_sub_ids || subscription_id;
                    end if;
                end if;
            end loop;

            perform set_config('role', null, true);

            -- Inner loop: per distinct selected_columns for this role
            for cols_record in
                select selected_columns
                from (select distinct selected_columns from unnest(subscriptions) s where s.claims_role = working_role) t
                order by coalesce(array_to_string(selected_columns, ','), '')
            loop
                working_selected_columns := cols_record.selected_columns;

                output = jsonb_build_object(
                    'schema', wal ->> 'schema',
                    'table', wal ->> 'table',
                    'type', action,
                    'commit_timestamp', to_char(
                        ((wal ->> 'timestamp')::timestamptz at time zone 'utc'),
                        'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
                    ),
                    'columns', (
                        select
                            jsonb_agg(
                                jsonb_build_object(
                                    'name', pa.attname,
                                    'type', pt.typname
                                )
                                order by pa.attnum asc
                            )
                        from
                            pg_attribute pa
                            join pg_type pt
                                on pa.atttypid = pt.oid
                            left join (
                                select unnest(conkey) as pkey_attnum
                                from pg_constraint
                                where conrelid = entity_ and contype = 'p'
                            ) pk on pk.pkey_attnum = pa.attnum
                        where
                            attrelid = entity_
                            and attnum > 0
                            and pg_catalog.has_column_privilege(working_role, entity_, pa.attname, 'SELECT')
                            and (working_selected_columns is null or pa.attname = any(working_selected_columns) or pk.pkey_attnum is not null)
                    )
                )
                -- Add "record" key for insert and update
                || case
                    when action in ('INSERT', 'UPDATE') then
                        jsonb_build_object(
                            'record',
                            (
                                select
                                    jsonb_object_agg(
                                        -- if unchanged toast, get column name and value from old record
                                        coalesce((c).name, (oc).name),
                                        case
                                            when (c).name is null then (oc).value
                                            else (c).value
                                        end
                                    )
                                from
                                    unnest(columns) c
                                    full outer join unnest(old_columns) oc
                                        on (c).name = (oc).name
                                where
                                    coalesce((c).is_selectable, (oc).is_selectable)
                                    and (working_selected_columns is null or coalesce((c).name, (oc).name) = any(working_selected_columns) or coalesce((c).is_pkey, (oc).is_pkey))
                                    and ( not error_record_exceeds_max_size or (octet_length((c).value::text) <= 64))
                            )
                        )
                    else '{}'::jsonb
                end
                -- Add "old_record" key for update and delete
                || case
                    when action = 'UPDATE' then
                        jsonb_build_object(
                                'old_record',
                                (
                                    select jsonb_object_agg((c).name, (c).value)
                                    from unnest(old_columns) c
                                    where
                                        (c).is_selectable
                                        and (working_selected_columns is null or (c).name = any(working_selected_columns) or (c).is_pkey)
                                        and ( not error_record_exceeds_max_size or (octet_length((c).value::text) <= 64))
                                )
                            )
                    when action = 'DELETE' then
                        jsonb_build_object(
                            'old_record',
                            (
                                select jsonb_object_agg((c).name, (c).value)
                                from unnest(old_columns) c
                                where
                                    (c).is_selectable
                                    and (working_selected_columns is null or (c).name = any(working_selected_columns) or (c).is_pkey)
                                    and ( not error_record_exceeds_max_size or (octet_length((c).value::text) <= 64))
                                    and ( not is_rls_enabled or (c).is_pkey ) -- if RLS enabled, we can't secure deletes so filter to pkey
                            )
                        )
                    else '{}'::jsonb
                end;

                -- Filter visible_role_sub_ids to those matching the current selected_columns group
                visible_to_subscription_ids = coalesce(
                    (
                        select array_agg(s.subscription_id)
                        from unnest(subscriptions) s
                        where s.claims_role = working_role
                          and (s.selected_columns is not distinct from working_selected_columns)
                          and s.subscription_id = any(visible_role_sub_ids)
                    ),
                    '{}'::uuid[]
                );

                return next (
                    output,
                    is_rls_enabled,
                    visible_to_subscription_ids,
                    case
                        when error_record_exceeds_max_size then array['Error 413: Payload Too Large']
                        else '{}'
                    end
                )::realtime.wal_rls;
            end loop;

        end if;
    end loop;

    perform set_config('role', null, true);
end;
$$;


--
-- Name: broadcast_changes(text, text, text, text, text, record, record, text); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.broadcast_changes(topic_name text, event_name text, operation text, table_name text, table_schema text, new record, old record, level text DEFAULT 'ROW'::text) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    -- Declare a variable to hold the JSONB representation of the row
    row_data jsonb := '{}'::jsonb;
BEGIN
    IF level = 'STATEMENT' THEN
        RAISE EXCEPTION 'function can only be triggered for each row, not for each statement';
    END IF;
    -- Check the operation type and handle accordingly
    IF operation = 'INSERT' OR operation = 'UPDATE' OR operation = 'DELETE' THEN
        row_data := jsonb_build_object('old_record', OLD, 'record', NEW, 'operation', operation, 'table', table_name, 'schema', table_schema);
        PERFORM realtime.send (row_data, event_name, topic_name);
    ELSE
        RAISE EXCEPTION 'Unexpected operation type: %', operation;
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Failed to process the row: %', SQLERRM;
END;

$$;


--
-- Name: build_prepared_statement_sql(text, regclass, realtime.wal_column[]); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.build_prepared_statement_sql(prepared_statement_name text, entity regclass, columns realtime.wal_column[]) RETURNS text
    LANGUAGE sql
    AS $$
      /*
      Builds a sql string that, if executed, creates a prepared statement to
      tests retrive a row from *entity* by its primary key columns.
      Example
          select realtime.build_prepared_statement_sql('public.notes', '{"id"}'::text[], '{"bigint"}'::text[])
      */
          select
      'prepare ' || prepared_statement_name || ' as
          select
              exists(
                  select
                      1
                  from
                      ' || entity || '
                  where
                      ' || string_agg(quote_ident(pkc.name) || '=' || quote_nullable(pkc.value #>> '{}') , ' and ') || '
              )'
          from
              unnest(columns) pkc
          where
              pkc.is_pkey
          group by
              entity
      $$;


--
-- Name: cast(text, regtype); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime."cast"(val text, type_ regtype) RETURNS jsonb
    LANGUAGE plpgsql IMMUTABLE
    AS $$
declare
  res jsonb;
begin
  if type_::text = 'bytea' then
    return to_jsonb(val);
  end if;
  execute format('select to_jsonb(%L::'|| type_::text || ')', val) into res;
  return res;
end
$$;


--
-- Name: check_equality_op(realtime.equality_op, regtype, text, text); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.check_equality_op(op realtime.equality_op, type_ regtype, val_1 text, val_2 text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
/*
Casts *val_1* and *val_2* as type *type_* and check the *op* condition for truthiness
*/
declare
    op_symbol text = (
        case
            when op = 'eq' then '='
            when op = 'neq' then '!='
            when op = 'lt' then '<'
            when op = 'lte' then '<='
            when op = 'gt' then '>'
            when op = 'gte' then '>='
            when op = 'in' then '= any'
            else 'UNKNOWN OP'
        end
    );
    res boolean;
begin
    execute format(
        'select %L::'|| type_::text || ' ' || op_symbol
        || ' ( %L::'
        || (
            case
                when op = 'in' then type_::text || '[]'
                else type_::text end
        )
        || ')', val_1, val_2) into res;
    return res;
end;
$$;


--
-- Name: is_visible_through_filters(realtime.wal_column[], realtime.user_defined_filter[]); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.is_visible_through_filters(columns realtime.wal_column[], filters realtime.user_defined_filter[]) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    AS $_$
/*
Should the record be visible (true) or filtered out (false) after *filters* are applied
*/
    select
        -- Default to allowed when no filters present
        $2 is null -- no filters. this should not happen because subscriptions has a default
        or array_length($2, 1) is null -- array length of an empty array is null
        or bool_and(
            coalesce(
                realtime.check_equality_op(
                    op:=f.op,
                    type_:=coalesce(
                        col.type_oid::regtype, -- null when wal2json version <= 2.4
                        col.type_name::regtype
                    ),
                    -- cast jsonb to text
                    val_1:=col.value #>> '{}',
                    val_2:=f.value
                ),
                false -- if null, filter does not match
            )
        )
    from
        unnest(filters) f
        join unnest(columns) col
            on f.column_name = col.name;
$_$;


--
-- Name: list_changes(name, name, integer, integer); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.list_changes(publication name, slot_name name, max_changes integer, max_record_bytes integer) RETURNS TABLE(wal jsonb, is_rls_enabled boolean, subscription_ids uuid[], errors text[], slot_changes_count bigint)
    LANGUAGE sql
    SET log_min_messages TO 'fatal'
    AS $$
  WITH pub AS (
    SELECT
      concat_ws(
        ',',
        CASE WHEN bool_or(pubinsert) THEN 'insert' ELSE NULL END,
        CASE WHEN bool_or(pubupdate) THEN 'update' ELSE NULL END,
        CASE WHEN bool_or(pubdelete) THEN 'delete' ELSE NULL END
      ) AS w2j_actions,
      coalesce(
        string_agg(
          realtime.quote_wal2json(format('%I.%I', schemaname, tablename)::regclass),
          ','
        ) filter (WHERE ppt.tablename IS NOT NULL),
        ''
      ) AS w2j_add_tables
    FROM pg_publication pp
    LEFT JOIN pg_publication_tables ppt ON pp.pubname = ppt.pubname
    WHERE pp.pubname = publication
    GROUP BY pp.pubname
    LIMIT 1
  ),
  -- MATERIALIZED ensures pg_logical_slot_get_changes is called exactly once
  w2j AS MATERIALIZED (
    SELECT x.*, pub.w2j_add_tables
    FROM pub,
         pg_logical_slot_get_changes(
           slot_name, null, max_changes,
           'include-pk', 'true',
           'include-transaction', 'false',
           'include-timestamp', 'true',
           'include-type-oids', 'true',
           'format-version', '2',
           'actions', pub.w2j_actions,
           'add-tables', pub.w2j_add_tables
         ) x
  ),
  slot_count AS (
    SELECT count(*)::bigint AS cnt
    FROM w2j
    WHERE w2j.w2j_add_tables <> ''
  ),
  rls_filtered AS (
    SELECT xyz.wal, xyz.is_rls_enabled, xyz.subscription_ids, xyz.errors
    FROM w2j,
         realtime.apply_rls(
           wal := w2j.data::jsonb,
           max_record_bytes := max_record_bytes
         ) xyz(wal, is_rls_enabled, subscription_ids, errors)
    WHERE w2j.w2j_add_tables <> ''
      AND xyz.subscription_ids[1] IS NOT NULL
  )
  SELECT rf.wal, rf.is_rls_enabled, rf.subscription_ids, rf.errors, sc.cnt
  FROM rls_filtered rf, slot_count sc

  UNION ALL

  SELECT null, null, null, null, sc.cnt
  FROM slot_count sc
  WHERE NOT EXISTS (SELECT 1 FROM rls_filtered)
$$;


--
-- Name: quote_wal2json(regclass); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.quote_wal2json(entity regclass) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
  SELECT
    realtime.wal2json_escape_identifier(nsp.nspname::text)
    || '.'
    || realtime.wal2json_escape_identifier(pc.relname::text)
  FROM pg_class pc
  JOIN pg_namespace nsp ON pc.relnamespace = nsp.oid
  WHERE pc.oid = entity
$$;


--
-- Name: send(jsonb, text, text, boolean); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.send(payload jsonb, event text, topic text, private boolean DEFAULT true) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
  generated_id uuid;
  final_payload jsonb;
BEGIN
  BEGIN
    generated_id := gen_random_uuid();

    -- Check if payload has an 'id' key, if not, add the generated UUID
    IF payload ? 'id' THEN
      final_payload := payload;
    ELSE
      final_payload := jsonb_set(payload, '{id}', to_jsonb(generated_id));
    END IF;

    -- Set the topic configuration
    EXECUTE format('SET LOCAL realtime.topic TO %L', topic);

    INSERT INTO realtime.messages (id, payload, event, topic, private, extension)
    VALUES (generated_id, final_payload, event, topic, private, 'broadcast');
  EXCEPTION
    WHEN OTHERS THEN
      RAISE WARNING 'WarnSendingBroadcastMessage: %', SQLERRM;
  END;
END;
$$;


--
-- Name: send_binary(bytea, text, text, boolean); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.send_binary(payload bytea, event text, topic text, private boolean DEFAULT true) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
  generated_id uuid;
BEGIN
  BEGIN
    generated_id := gen_random_uuid();

    EXECUTE format('SET LOCAL realtime.topic TO %L', topic);

    INSERT INTO realtime.messages (id, binary_payload, event, topic, private, extension)
    VALUES (generated_id, payload, event, topic, private, 'broadcast');
  EXCEPTION
    WHEN OTHERS THEN
      RAISE WARNING 'WarnSendingBroadcastMessage: %', SQLERRM;
  END;
END;
$$;


--
-- Name: subscription_check_filters(); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.subscription_check_filters() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
declare
    col_names text[] = coalesce(
            array_agg(a.attname order by a.attnum),
            '{}'::text[]
        )
        from
            pg_catalog.pg_attribute a
        where
            a.attrelid = new.entity
            and a.attnum > 0
            and not a.attisdropped
            and pg_catalog.has_column_privilege(
                (new.claims ->> 'role'),
                a.attrelid,
                a.attnum,
                'SELECT'
            );
    filter realtime.user_defined_filter;
    col_type regtype;
    in_val jsonb;
    selected_col text;
begin
    for filter in select * from unnest(new.filters) loop
        if not filter.column_name = any(col_names) then
            raise exception 'invalid column for filter %', filter.column_name;
        end if;

        col_type = (
            select atttypid::regtype
            from pg_catalog.pg_attribute
            where attrelid = new.entity
                  and attname = filter.column_name
        );
        if col_type is null then
            raise exception 'failed to lookup type for column %', filter.column_name;
        end if;

        if filter.op = 'in'::realtime.equality_op then
            in_val = realtime.cast(filter.value, (col_type::text || '[]')::regtype);
            if coalesce(jsonb_array_length(in_val), 0) > 100 then
                raise exception 'too many values for `in` filter. Maximum 100';
            end if;
        else
            perform realtime.cast(filter.value, col_type);
        end if;
    end loop;

    if new.selected_columns is not null then
        for selected_col in select * from unnest(new.selected_columns) loop
            if not selected_col = any(col_names) then
                raise exception 'invalid column for select %', selected_col;
            end if;
        end loop;
    end if;

    new.filters = coalesce(
        array_agg(f order by f.column_name, f.op, f.value),
        '{}'
    ) from unnest(new.filters) f;

    new.selected_columns = (
        select array_agg(c order by c)
        from unnest(new.selected_columns) c
    );

    return new;
end;
$$;


--
-- Name: to_regrole(text); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.to_regrole(role_name text) RETURNS regrole
    LANGUAGE sql IMMUTABLE
    AS $$ select role_name::regrole $$;


--
-- Name: topic(); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.topic() RETURNS text
    LANGUAGE sql STABLE
    AS $$
select nullif(current_setting('realtime.topic', true), '')::text;
$$;


--
-- Name: wal2json_escape_identifier(text); Type: FUNCTION; Schema: realtime; Owner: -
--

CREATE FUNCTION realtime.wal2json_escape_identifier(name text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
  -- Prefix `\`, `,`, `.`, and any whitespace with `\`
  SELECT regexp_replace(name, '([\\,.[:space:]])', '\\\1', 'g')
$$;


--
-- Name: allow_any_operation(text[]); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.allow_any_operation(expected_operations text[]) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
  WITH current_operation AS (
    SELECT storage.operation() AS raw_operation
  ),
  normalized AS (
    SELECT CASE
      WHEN raw_operation LIKE 'storage.%' THEN substr(raw_operation, 9)
      ELSE raw_operation
    END AS current_operation
    FROM current_operation
  )
  SELECT EXISTS (
    SELECT 1
    FROM normalized n
    CROSS JOIN LATERAL unnest(expected_operations) AS expected_operation
    WHERE expected_operation IS NOT NULL
      AND expected_operation <> ''
      AND n.current_operation = CASE
        WHEN expected_operation LIKE 'storage.%' THEN substr(expected_operation, 9)
        ELSE expected_operation
      END
  );
$$;


--
-- Name: allow_only_operation(text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.allow_only_operation(expected_operation text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
  WITH current_operation AS (
    SELECT storage.operation() AS raw_operation
  ),
  normalized AS (
    SELECT
      CASE
        WHEN raw_operation LIKE 'storage.%' THEN substr(raw_operation, 9)
        ELSE raw_operation
      END AS current_operation,
      CASE
        WHEN expected_operation LIKE 'storage.%' THEN substr(expected_operation, 9)
        ELSE expected_operation
      END AS requested_operation
    FROM current_operation
  )
  SELECT CASE
    WHEN requested_operation IS NULL OR requested_operation = '' THEN FALSE
    ELSE COALESCE(current_operation = requested_operation, FALSE)
  END
  FROM normalized;
$$;


--
-- Name: can_insert_object(text, text, uuid, jsonb); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.can_insert_object(bucketid text, name text, owner uuid, metadata jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
  INSERT INTO "storage"."objects" ("bucket_id", "name", "owner", "metadata") VALUES (bucketid, name, owner, metadata);
  -- hack to rollback the successful insert
  RAISE sqlstate 'PT200' using
  message = 'ROLLBACK',
  detail = 'rollback successful insert';
END
$$;


--
-- Name: enforce_bucket_name_length(); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.enforce_bucket_name_length() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    if length(new.name) > 100 then
        raise exception 'bucket name "%" is too long (% characters). Max is 100.', new.name, length(new.name);
    end if;
    return new;
end;
$$;


--
-- Name: extension(text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.extension(name text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    _parts text[];
    _filename text;
BEGIN
    -- Split on "/" to get path segments
    SELECT string_to_array(name, '/') INTO _parts;
    -- Get the last path segment (the actual filename)
    SELECT _parts[array_length(_parts, 1)] INTO _filename;
    -- Extract extension: reverse, split on '.', then reverse again
    RETURN reverse(split_part(reverse(_filename), '.', 1));
END
$$;


--
-- Name: filename(text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.filename(name text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
_parts text[];
BEGIN
	select string_to_array(name, '/') into _parts;
	return _parts[array_length(_parts,1)];
END
$$;


--
-- Name: foldername(text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.foldername(name text) RETURNS text[]
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    _parts text[];
BEGIN
    -- Split on "/" to get path segments
    SELECT string_to_array(name, '/') INTO _parts;
    -- Return everything except the last segment
    RETURN _parts[1 : array_length(_parts,1) - 1];
END
$$;


--
-- Name: get_common_prefix(text, text, text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.get_common_prefix(p_key text, p_prefix text, p_delimiter text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$
SELECT CASE
    WHEN position(p_delimiter IN substring(p_key FROM length(p_prefix) + 1)) > 0
    THEN left(p_key, length(p_prefix) + position(p_delimiter IN substring(p_key FROM length(p_prefix) + 1)))
    ELSE NULL
END;
$$;


--
-- Name: get_size_by_bucket(); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.get_size_by_bucket() RETURNS TABLE(size bigint, bucket_id text)
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
    return query
        select sum((metadata->>'size')::bigint)::bigint as size, obj.bucket_id
        from "storage".objects as obj
        group by obj.bucket_id;
END
$$;


--
-- Name: list_multipart_uploads_with_delimiter(text, text, text, integer, text, text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.list_multipart_uploads_with_delimiter(bucket_id text, prefix_param text, delimiter_param text, max_keys integer DEFAULT 100, next_key_token text DEFAULT ''::text, next_upload_token text DEFAULT ''::text) RETURNS TABLE(key text, id text, created_at timestamp with time zone)
    LANGUAGE plpgsql
    AS $_$
BEGIN
    RETURN QUERY EXECUTE
        'SELECT DISTINCT ON(key COLLATE "C") * from (
            SELECT
                CASE
                    WHEN position($2 IN substring(key from length($1) + 1)) > 0 THEN
                        substring(key from 1 for length($1) + position($2 IN substring(key from length($1) + 1)))
                    ELSE
                        key
                END AS key, id, created_at
            FROM
                storage.s3_multipart_uploads
            WHERE
                bucket_id = $5 AND
                key ILIKE $1 || ''%'' AND
                CASE
                    WHEN $4 != '''' AND $6 = '''' THEN
                        CASE
                            WHEN position($2 IN substring(key from length($1) + 1)) > 0 THEN
                                substring(key from 1 for length($1) + position($2 IN substring(key from length($1) + 1))) COLLATE "C" > $4
                            ELSE
                                key COLLATE "C" > $4
                            END
                    ELSE
                        true
                END AND
                CASE
                    WHEN $6 != '''' THEN
                        id COLLATE "C" > $6
                    ELSE
                        true
                    END
            ORDER BY
                key COLLATE "C" ASC, created_at ASC) as e order by key COLLATE "C" LIMIT $3'
        USING prefix_param, delimiter_param, max_keys, next_key_token, bucket_id, next_upload_token;
END;
$_$;


--
-- Name: list_objects_with_delimiter(text, text, text, integer, text, text, text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.list_objects_with_delimiter(_bucket_id text, prefix_param text, delimiter_param text, max_keys integer DEFAULT 100, start_after text DEFAULT ''::text, next_token text DEFAULT ''::text, sort_order text DEFAULT 'asc'::text) RETURNS TABLE(name text, id uuid, metadata jsonb, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone)
    LANGUAGE plpgsql STABLE
    AS $_$
DECLARE
    v_peek_name TEXT;
    v_current RECORD;
    v_common_prefix TEXT;

    -- Configuration
    v_is_asc BOOLEAN;
    v_prefix TEXT;
    v_start TEXT;
    v_upper_bound TEXT;
    v_file_batch_size INT;

    -- Seek state
    v_next_seek TEXT;
    v_count INT := 0;

    -- Dynamic SQL for batch query only
    v_batch_query TEXT;

BEGIN
    -- ========================================================================
    -- INITIALIZATION
    -- ========================================================================
    v_is_asc := lower(coalesce(sort_order, 'asc')) = 'asc';
    v_prefix := coalesce(prefix_param, '');
    v_start := CASE WHEN coalesce(next_token, '') <> '' THEN next_token ELSE coalesce(start_after, '') END;
    v_file_batch_size := LEAST(GREATEST(max_keys * 2, 100), 1000);

    -- Calculate upper bound for prefix filtering (bytewise, using COLLATE "C")
    IF v_prefix = '' THEN
        v_upper_bound := NULL;
    ELSIF right(v_prefix, 1) = delimiter_param THEN
        v_upper_bound := left(v_prefix, -1) || chr(ascii(delimiter_param) + 1);
    ELSE
        v_upper_bound := left(v_prefix, -1) || chr(ascii(right(v_prefix, 1)) + 1);
    END IF;

    -- Build batch query (dynamic SQL - called infrequently, amortized over many rows)
    IF v_is_asc THEN
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" >= $2 ' ||
                'AND o.name COLLATE "C" < $3 ORDER BY o.name COLLATE "C" ASC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" >= $2 ' ||
                'ORDER BY o.name COLLATE "C" ASC LIMIT $4';
        END IF;
    ELSE
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" < $2 ' ||
                'AND o.name COLLATE "C" >= $3 ORDER BY o.name COLLATE "C" DESC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" < $2 ' ||
                'ORDER BY o.name COLLATE "C" DESC LIMIT $4';
        END IF;
    END IF;

    -- ========================================================================
    -- SEEK INITIALIZATION: Determine starting position
    -- ========================================================================
    IF v_start = '' THEN
        IF v_is_asc THEN
            v_next_seek := v_prefix;
        ELSE
            -- DESC without cursor: find the last item in range
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_next_seek FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_prefix AND o.name COLLATE "C" < v_upper_bound
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSIF v_prefix <> '' THEN
                SELECT o.name INTO v_next_seek FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_prefix
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSE
                SELECT o.name INTO v_next_seek FROM storage.objects o
                WHERE o.bucket_id = _bucket_id
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            END IF;

            IF v_next_seek IS NOT NULL THEN
                v_next_seek := v_next_seek || delimiter_param;
            ELSE
                RETURN;
            END IF;
        END IF;
    ELSE
        -- Cursor provided: determine if it refers to a folder or leaf
        IF EXISTS (
            SELECT 1 FROM storage.objects o
            WHERE o.bucket_id = _bucket_id
              AND o.name COLLATE "C" LIKE v_start || delimiter_param || '%'
            LIMIT 1
        ) THEN
            -- Cursor refers to a folder
            IF v_is_asc THEN
                v_next_seek := v_start || chr(ascii(delimiter_param) + 1);
            ELSE
                v_next_seek := v_start || delimiter_param;
            END IF;
        ELSE
            -- Cursor refers to a leaf object
            IF v_is_asc THEN
                v_next_seek := v_start || delimiter_param;
            ELSE
                v_next_seek := v_start;
            END IF;
        END IF;
    END IF;

    -- ========================================================================
    -- MAIN LOOP: Hybrid peek-then-batch algorithm
    -- Uses STATIC SQL for peek (hot path) and DYNAMIC SQL for batch
    -- ========================================================================
    LOOP
        EXIT WHEN v_count >= max_keys;

        -- STEP 1: PEEK using STATIC SQL (plan cached, very fast)
        IF v_is_asc THEN
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_next_seek AND o.name COLLATE "C" < v_upper_bound
                ORDER BY o.name COLLATE "C" ASC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_next_seek
                ORDER BY o.name COLLATE "C" ASC LIMIT 1;
            END IF;
        ELSE
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" < v_next_seek AND o.name COLLATE "C" >= v_prefix
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSIF v_prefix <> '' THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" < v_next_seek AND o.name COLLATE "C" >= v_prefix
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" < v_next_seek
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            END IF;
        END IF;

        EXIT WHEN v_peek_name IS NULL;

        -- STEP 2: Check if this is a FOLDER or FILE
        v_common_prefix := storage.get_common_prefix(v_peek_name, v_prefix, delimiter_param);

        IF v_common_prefix IS NOT NULL THEN
            -- FOLDER: Emit and skip to next folder (no heap access needed)
            name := rtrim(v_common_prefix, delimiter_param);
            id := NULL;
            updated_at := NULL;
            created_at := NULL;
            last_accessed_at := NULL;
            metadata := NULL;
            RETURN NEXT;
            v_count := v_count + 1;

            -- Advance seek past the folder range
            IF v_is_asc THEN
                v_next_seek := left(v_common_prefix, -1) || chr(ascii(delimiter_param) + 1);
            ELSE
                v_next_seek := v_common_prefix;
            END IF;
        ELSE
            -- FILE: Batch fetch using DYNAMIC SQL (overhead amortized over many rows)
            -- For ASC: upper_bound is the exclusive upper limit (< condition)
            -- For DESC: prefix is the inclusive lower limit (>= condition)
            FOR v_current IN EXECUTE v_batch_query USING _bucket_id, v_next_seek,
                CASE WHEN v_is_asc THEN COALESCE(v_upper_bound, v_prefix) ELSE v_prefix END, v_file_batch_size
            LOOP
                v_common_prefix := storage.get_common_prefix(v_current.name, v_prefix, delimiter_param);

                IF v_common_prefix IS NOT NULL THEN
                    -- Hit a folder: exit batch, let peek handle it
                    v_next_seek := v_current.name;
                    EXIT;
                END IF;

                -- Emit file
                name := v_current.name;
                id := v_current.id;
                updated_at := v_current.updated_at;
                created_at := v_current.created_at;
                last_accessed_at := v_current.last_accessed_at;
                metadata := v_current.metadata;
                RETURN NEXT;
                v_count := v_count + 1;

                -- Advance seek past this file
                IF v_is_asc THEN
                    v_next_seek := v_current.name || delimiter_param;
                ELSE
                    v_next_seek := v_current.name;
                END IF;

                EXIT WHEN v_count >= max_keys;
            END LOOP;
        END IF;
    END LOOP;
END;
$_$;


--
-- Name: operation(); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.operation() RETURNS text
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
    RETURN current_setting('storage.operation', true);
END;
$$;


--
-- Name: protect_delete(); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.protect_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Check if storage.allow_delete_query is set to 'true'
    IF COALESCE(current_setting('storage.allow_delete_query', true), 'false') != 'true' THEN
        RAISE EXCEPTION 'Direct deletion from storage tables is not allowed. Use the Storage API instead.'
            USING HINT = 'This prevents accidental data loss from orphaned objects.',
                  ERRCODE = '42501';
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: search(text, text, integer, integer, integer, text, text, text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.search(prefix text, bucketname text, limits integer DEFAULT 100, levels integer DEFAULT 1, offsets integer DEFAULT 0, search text DEFAULT ''::text, sortcolumn text DEFAULT 'name'::text, sortorder text DEFAULT 'asc'::text) RETURNS TABLE(name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb)
    LANGUAGE plpgsql STABLE
    AS $_$
DECLARE
    v_peek_name TEXT;
    v_current RECORD;
    v_common_prefix TEXT;
    v_delimiter CONSTANT TEXT := '/';

    -- Configuration
    v_limit INT;
    v_prefix TEXT;
    v_prefix_lower TEXT;
    v_is_asc BOOLEAN;
    v_order_by TEXT;
    v_sort_order TEXT;
    v_upper_bound TEXT;
    v_file_batch_size INT;

    -- Dynamic SQL for batch query only
    v_batch_query TEXT;

    -- Seek state
    v_next_seek TEXT;
    v_count INT := 0;
    v_skipped INT := 0;
BEGIN
    -- ========================================================================
    -- INITIALIZATION
    -- ========================================================================
    v_limit := LEAST(coalesce(limits, 100), 1500);
    v_prefix := coalesce(prefix, '') || coalesce(search, '');
    v_prefix_lower := lower(v_prefix);
    v_is_asc := lower(coalesce(sortorder, 'asc')) = 'asc';
    v_file_batch_size := LEAST(GREATEST(v_limit * 2, 100), 1000);

    -- Validate sort column
    CASE lower(coalesce(sortcolumn, 'name'))
        WHEN 'name' THEN v_order_by := 'name';
        WHEN 'updated_at' THEN v_order_by := 'updated_at';
        WHEN 'created_at' THEN v_order_by := 'created_at';
        WHEN 'last_accessed_at' THEN v_order_by := 'last_accessed_at';
        ELSE v_order_by := 'name';
    END CASE;

    v_sort_order := CASE WHEN v_is_asc THEN 'asc' ELSE 'desc' END;

    -- ========================================================================
    -- NON-NAME SORTING: Use path_tokens approach (unchanged)
    -- ========================================================================
    IF v_order_by != 'name' THEN
        RETURN QUERY EXECUTE format(
            $sql$
            WITH folders AS (
                SELECT path_tokens[$1] AS folder
                FROM storage.objects
                WHERE objects.name ILIKE $2 || '%%'
                  AND bucket_id = $3
                  AND array_length(objects.path_tokens, 1) <> $1
                GROUP BY folder
                ORDER BY folder %s
            )
            (SELECT folder AS "name",
                   NULL::uuid AS id,
                   NULL::timestamptz AS updated_at,
                   NULL::timestamptz AS created_at,
                   NULL::timestamptz AS last_accessed_at,
                   NULL::jsonb AS metadata FROM folders)
            UNION ALL
            (SELECT path_tokens[$1] AS "name",
                   id, updated_at, created_at, last_accessed_at, metadata
             FROM storage.objects
             WHERE objects.name ILIKE $2 || '%%'
               AND bucket_id = $3
               AND array_length(objects.path_tokens, 1) = $1
             ORDER BY %I %s)
            LIMIT $4 OFFSET $5
            $sql$, v_sort_order, v_order_by, v_sort_order
        ) USING levels, v_prefix, bucketname, v_limit, offsets;
        RETURN;
    END IF;

    -- ========================================================================
    -- NAME SORTING: Hybrid skip-scan with batch optimization
    -- ========================================================================

    -- Calculate upper bound for prefix filtering
    IF v_prefix_lower = '' THEN
        v_upper_bound := NULL;
    ELSIF right(v_prefix_lower, 1) = v_delimiter THEN
        v_upper_bound := left(v_prefix_lower, -1) || chr(ascii(v_delimiter) + 1);
    ELSE
        v_upper_bound := left(v_prefix_lower, -1) || chr(ascii(right(v_prefix_lower, 1)) + 1);
    END IF;

    -- Build batch query (dynamic SQL - called infrequently, amortized over many rows)
    IF v_is_asc THEN
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" >= $2 ' ||
                'AND lower(o.name) COLLATE "C" < $3 ORDER BY lower(o.name) COLLATE "C" ASC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" >= $2 ' ||
                'ORDER BY lower(o.name) COLLATE "C" ASC LIMIT $4';
        END IF;
    ELSE
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" < $2 ' ||
                'AND lower(o.name) COLLATE "C" >= $3 ORDER BY lower(o.name) COLLATE "C" DESC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" < $2 ' ||
                'ORDER BY lower(o.name) COLLATE "C" DESC LIMIT $4';
        END IF;
    END IF;

    -- Initialize seek position
    IF v_is_asc THEN
        v_next_seek := v_prefix_lower;
    ELSE
        -- DESC: find the last item in range first (static SQL)
        IF v_upper_bound IS NOT NULL THEN
            SELECT o.name INTO v_peek_name FROM storage.objects o
            WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_prefix_lower AND lower(o.name) COLLATE "C" < v_upper_bound
            ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
        ELSIF v_prefix_lower <> '' THEN
            SELECT o.name INTO v_peek_name FROM storage.objects o
            WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_prefix_lower
            ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
        ELSE
            SELECT o.name INTO v_peek_name FROM storage.objects o
            WHERE o.bucket_id = bucketname
            ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
        END IF;

        IF v_peek_name IS NOT NULL THEN
            v_next_seek := lower(v_peek_name) || v_delimiter;
        ELSE
            RETURN;
        END IF;
    END IF;

    -- ========================================================================
    -- MAIN LOOP: Hybrid peek-then-batch algorithm
    -- Uses STATIC SQL for peek (hot path) and DYNAMIC SQL for batch
    -- ========================================================================
    LOOP
        EXIT WHEN v_count >= v_limit;

        -- STEP 1: PEEK using STATIC SQL (plan cached, very fast)
        IF v_is_asc THEN
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_next_seek AND lower(o.name) COLLATE "C" < v_upper_bound
                ORDER BY lower(o.name) COLLATE "C" ASC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_next_seek
                ORDER BY lower(o.name) COLLATE "C" ASC LIMIT 1;
            END IF;
        ELSE
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" < v_next_seek AND lower(o.name) COLLATE "C" >= v_prefix_lower
                ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
            ELSIF v_prefix_lower <> '' THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" < v_next_seek AND lower(o.name) COLLATE "C" >= v_prefix_lower
                ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" < v_next_seek
                ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
            END IF;
        END IF;

        EXIT WHEN v_peek_name IS NULL;

        -- STEP 2: Check if this is a FOLDER or FILE
        v_common_prefix := storage.get_common_prefix(lower(v_peek_name), v_prefix_lower, v_delimiter);

        IF v_common_prefix IS NOT NULL THEN
            -- FOLDER: Handle offset, emit if needed, skip to next folder
            IF v_skipped < offsets THEN
                v_skipped := v_skipped + 1;
            ELSE
                name := split_part(rtrim(storage.get_common_prefix(v_peek_name, v_prefix, v_delimiter), v_delimiter), v_delimiter, levels);
                id := NULL;
                updated_at := NULL;
                created_at := NULL;
                last_accessed_at := NULL;
                metadata := NULL;
                RETURN NEXT;
                v_count := v_count + 1;
            END IF;

            -- Advance seek past the folder range
            IF v_is_asc THEN
                v_next_seek := lower(left(v_common_prefix, -1)) || chr(ascii(v_delimiter) + 1);
            ELSE
                v_next_seek := lower(v_common_prefix);
            END IF;
        ELSE
            -- FILE: Batch fetch using DYNAMIC SQL (overhead amortized over many rows)
            -- For ASC: upper_bound is the exclusive upper limit (< condition)
            -- For DESC: prefix_lower is the inclusive lower limit (>= condition)
            FOR v_current IN EXECUTE v_batch_query
                USING bucketname, v_next_seek,
                    CASE WHEN v_is_asc THEN COALESCE(v_upper_bound, v_prefix_lower) ELSE v_prefix_lower END, v_file_batch_size
            LOOP
                v_common_prefix := storage.get_common_prefix(lower(v_current.name), v_prefix_lower, v_delimiter);

                IF v_common_prefix IS NOT NULL THEN
                    -- Hit a folder: exit batch, let peek handle it
                    v_next_seek := lower(v_current.name);
                    EXIT;
                END IF;

                -- Handle offset skipping
                IF v_skipped < offsets THEN
                    v_skipped := v_skipped + 1;
                ELSE
                    -- Emit file
                    name := split_part(v_current.name, v_delimiter, levels);
                    id := v_current.id;
                    updated_at := v_current.updated_at;
                    created_at := v_current.created_at;
                    last_accessed_at := v_current.last_accessed_at;
                    metadata := v_current.metadata;
                    RETURN NEXT;
                    v_count := v_count + 1;
                END IF;

                -- Advance seek past this file
                IF v_is_asc THEN
                    v_next_seek := lower(v_current.name) || v_delimiter;
                ELSE
                    v_next_seek := lower(v_current.name);
                END IF;

                EXIT WHEN v_count >= v_limit;
            END LOOP;
        END IF;
    END LOOP;
END;
$_$;


--
-- Name: search_by_timestamp(text, text, integer, integer, text, text, text, text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.search_by_timestamp(p_prefix text, p_bucket_id text, p_limit integer, p_level integer, p_start_after text, p_sort_order text, p_sort_column text, p_sort_column_after text) RETURNS TABLE(key text, name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb)
    LANGUAGE plpgsql STABLE
    AS $_$
DECLARE
    v_cursor_op text;
    v_query text;
    v_prefix text;
BEGIN
    v_prefix := coalesce(p_prefix, '');

    IF p_sort_order = 'asc' THEN
        v_cursor_op := '>';
    ELSE
        v_cursor_op := '<';
    END IF;

    v_query := format($sql$
        WITH raw_objects AS (
            SELECT
                o.name AS obj_name,
                o.id AS obj_id,
                o.updated_at AS obj_updated_at,
                o.created_at AS obj_created_at,
                o.last_accessed_at AS obj_last_accessed_at,
                o.metadata AS obj_metadata,
                storage.get_common_prefix(o.name, $1, '/') AS common_prefix
            FROM storage.objects o
            WHERE o.bucket_id = $2
              AND o.name COLLATE "C" LIKE $1 || '%%'
        ),
        -- Aggregate common prefixes (folders)
        -- Both created_at and updated_at use MIN(obj_created_at) to match the old prefixes table behavior
        aggregated_prefixes AS (
            SELECT
                rtrim(common_prefix, '/') AS name,
                NULL::uuid AS id,
                MIN(obj_created_at) AS updated_at,
                MIN(obj_created_at) AS created_at,
                NULL::timestamptz AS last_accessed_at,
                NULL::jsonb AS metadata,
                TRUE AS is_prefix
            FROM raw_objects
            WHERE common_prefix IS NOT NULL
            GROUP BY common_prefix
        ),
        leaf_objects AS (
            SELECT
                obj_name AS name,
                obj_id AS id,
                obj_updated_at AS updated_at,
                obj_created_at AS created_at,
                obj_last_accessed_at AS last_accessed_at,
                obj_metadata AS metadata,
                FALSE AS is_prefix
            FROM raw_objects
            WHERE common_prefix IS NULL
        ),
        combined AS (
            SELECT * FROM aggregated_prefixes
            UNION ALL
            SELECT * FROM leaf_objects
        ),
        filtered AS (
            SELECT *
            FROM combined
            WHERE (
                $5 = ''
                OR ROW(
                    date_trunc('milliseconds', %I),
                    name COLLATE "C"
                ) %s ROW(
                    COALESCE(NULLIF($6, '')::timestamptz, 'epoch'::timestamptz),
                    $5
                )
            )
        )
        SELECT
            split_part(name, '/', $3) AS key,
            name,
            id,
            updated_at,
            created_at,
            last_accessed_at,
            metadata
        FROM filtered
        ORDER BY
            COALESCE(date_trunc('milliseconds', %I), 'epoch'::timestamptz) %s,
            name COLLATE "C" %s
        LIMIT $4
    $sql$,
        p_sort_column,
        v_cursor_op,
        p_sort_column,
        p_sort_order,
        p_sort_order
    );

    RETURN QUERY EXECUTE v_query
    USING v_prefix, p_bucket_id, p_level, p_limit, p_start_after, p_sort_column_after;
END;
$_$;


--
-- Name: search_v2(text, text, integer, integer, text, text, text, text); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.search_v2(prefix text, bucket_name text, limits integer DEFAULT 100, levels integer DEFAULT 1, start_after text DEFAULT ''::text, sort_order text DEFAULT 'asc'::text, sort_column text DEFAULT 'name'::text, sort_column_after text DEFAULT ''::text) RETURNS TABLE(key text, name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb)
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_sort_col text;
    v_sort_ord text;
    v_limit int;
BEGIN
    -- Cap limit to maximum of 1500 records
    v_limit := LEAST(coalesce(limits, 100), 1500);

    -- Validate and normalize sort_order
    v_sort_ord := lower(coalesce(sort_order, 'asc'));
    IF v_sort_ord NOT IN ('asc', 'desc') THEN
        v_sort_ord := 'asc';
    END IF;

    -- Validate and normalize sort_column
    v_sort_col := lower(coalesce(sort_column, 'name'));
    IF v_sort_col NOT IN ('name', 'updated_at', 'created_at') THEN
        v_sort_col := 'name';
    END IF;

    -- Route to appropriate implementation
    IF v_sort_col = 'name' THEN
        -- Use list_objects_with_delimiter for name sorting (most efficient: O(k * log n))
        RETURN QUERY
        SELECT
            split_part(l.name, '/', levels) AS key,
            l.name AS name,
            l.id,
            l.updated_at,
            l.created_at,
            l.last_accessed_at,
            l.metadata
        FROM storage.list_objects_with_delimiter(
            bucket_name,
            coalesce(prefix, ''),
            '/',
            v_limit,
            start_after,
            '',
            v_sort_ord
        ) l;
    ELSE
        -- Use aggregation approach for timestamp sorting
        -- Not efficient for large datasets but supports correct pagination
        RETURN QUERY SELECT * FROM storage.search_by_timestamp(
            prefix, bucket_name, v_limit, levels, start_after,
            v_sort_ord, v_sort_col, sort_column_after
        );
    END IF;
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: storage; Owner: -
--

CREATE FUNCTION storage.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW; 
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_log_entries; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.audit_log_entries (
    instance_id uuid,
    id uuid NOT NULL,
    payload json,
    created_at timestamp with time zone,
    ip_address character varying(64) DEFAULT ''::character varying NOT NULL
);


--
-- Name: TABLE audit_log_entries; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.audit_log_entries IS 'Auth: Audit trail for user actions.';


--
-- Name: custom_oauth_providers; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.custom_oauth_providers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider_type text NOT NULL,
    identifier text NOT NULL,
    name text NOT NULL,
    client_id text NOT NULL,
    client_secret text NOT NULL,
    acceptable_client_ids text[] DEFAULT '{}'::text[] NOT NULL,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    pkce_enabled boolean DEFAULT true NOT NULL,
    attribute_mapping jsonb DEFAULT '{}'::jsonb NOT NULL,
    authorization_params jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    email_optional boolean DEFAULT false NOT NULL,
    issuer text,
    discovery_url text,
    skip_nonce_check boolean DEFAULT false NOT NULL,
    cached_discovery jsonb,
    discovery_cached_at timestamp with time zone,
    authorization_url text,
    token_url text,
    userinfo_url text,
    jwks_uri text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    custom_claims_allowlist text[] DEFAULT '{}'::text[] NOT NULL,
    CONSTRAINT custom_oauth_providers_authorization_url_https CHECK (((authorization_url IS NULL) OR (authorization_url ~~ 'https://%'::text))),
    CONSTRAINT custom_oauth_providers_authorization_url_length CHECK (((authorization_url IS NULL) OR (char_length(authorization_url) <= 2048))),
    CONSTRAINT custom_oauth_providers_client_id_length CHECK (((char_length(client_id) >= 1) AND (char_length(client_id) <= 512))),
    CONSTRAINT custom_oauth_providers_discovery_url_length CHECK (((discovery_url IS NULL) OR (char_length(discovery_url) <= 2048))),
    CONSTRAINT custom_oauth_providers_identifier_format CHECK ((identifier ~ '^[a-z0-9][a-z0-9:-]{0,48}[a-z0-9]$'::text)),
    CONSTRAINT custom_oauth_providers_issuer_length CHECK (((issuer IS NULL) OR ((char_length(issuer) >= 1) AND (char_length(issuer) <= 2048)))),
    CONSTRAINT custom_oauth_providers_jwks_uri_https CHECK (((jwks_uri IS NULL) OR (jwks_uri ~~ 'https://%'::text))),
    CONSTRAINT custom_oauth_providers_jwks_uri_length CHECK (((jwks_uri IS NULL) OR (char_length(jwks_uri) <= 2048))),
    CONSTRAINT custom_oauth_providers_name_length CHECK (((char_length(name) >= 1) AND (char_length(name) <= 100))),
    CONSTRAINT custom_oauth_providers_oauth2_requires_endpoints CHECK (((provider_type <> 'oauth2'::text) OR ((authorization_url IS NOT NULL) AND (token_url IS NOT NULL) AND (userinfo_url IS NOT NULL)))),
    CONSTRAINT custom_oauth_providers_oidc_discovery_url_https CHECK (((provider_type <> 'oidc'::text) OR (discovery_url IS NULL) OR (discovery_url ~~ 'https://%'::text))),
    CONSTRAINT custom_oauth_providers_oidc_issuer_https CHECK (((provider_type <> 'oidc'::text) OR (issuer IS NULL) OR (issuer ~~ 'https://%'::text))),
    CONSTRAINT custom_oauth_providers_oidc_requires_issuer CHECK (((provider_type <> 'oidc'::text) OR (issuer IS NOT NULL))),
    CONSTRAINT custom_oauth_providers_provider_type_check CHECK ((provider_type = ANY (ARRAY['oauth2'::text, 'oidc'::text]))),
    CONSTRAINT custom_oauth_providers_token_url_https CHECK (((token_url IS NULL) OR (token_url ~~ 'https://%'::text))),
    CONSTRAINT custom_oauth_providers_token_url_length CHECK (((token_url IS NULL) OR (char_length(token_url) <= 2048))),
    CONSTRAINT custom_oauth_providers_userinfo_url_https CHECK (((userinfo_url IS NULL) OR (userinfo_url ~~ 'https://%'::text))),
    CONSTRAINT custom_oauth_providers_userinfo_url_length CHECK (((userinfo_url IS NULL) OR (char_length(userinfo_url) <= 2048)))
);


--
-- Name: flow_state; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.flow_state (
    id uuid NOT NULL,
    user_id uuid,
    auth_code text,
    code_challenge_method auth.code_challenge_method,
    code_challenge text,
    provider_type text NOT NULL,
    provider_access_token text,
    provider_refresh_token text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    authentication_method text NOT NULL,
    auth_code_issued_at timestamp with time zone,
    invite_token text,
    referrer text,
    oauth_client_state_id uuid,
    linking_target_id uuid,
    email_optional boolean DEFAULT false NOT NULL
);


--
-- Name: TABLE flow_state; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.flow_state IS 'Stores metadata for all OAuth/SSO login flows';


--
-- Name: identities; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.identities (
    provider_id text NOT NULL,
    user_id uuid NOT NULL,
    identity_data jsonb NOT NULL,
    provider text NOT NULL,
    last_sign_in_at timestamp with time zone,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    email text GENERATED ALWAYS AS (lower((identity_data ->> 'email'::text))) STORED,
    id uuid DEFAULT gen_random_uuid() NOT NULL
);


--
-- Name: TABLE identities; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.identities IS 'Auth: Stores identities associated to a user.';


--
-- Name: COLUMN identities.email; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON COLUMN auth.identities.email IS 'Auth: Email is a generated column that references the optional email property in the identity_data';


--
-- Name: instances; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.instances (
    id uuid NOT NULL,
    uuid uuid,
    raw_base_config text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


--
-- Name: TABLE instances; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.instances IS 'Auth: Manages users across multiple sites.';


--
-- Name: mfa_amr_claims; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.mfa_amr_claims (
    session_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    authentication_method text NOT NULL,
    id uuid NOT NULL
);


--
-- Name: TABLE mfa_amr_claims; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.mfa_amr_claims IS 'auth: stores authenticator method reference claims for multi factor authentication';


--
-- Name: mfa_challenges; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.mfa_challenges (
    id uuid NOT NULL,
    factor_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    verified_at timestamp with time zone,
    ip_address inet NOT NULL,
    otp_code text,
    web_authn_session_data jsonb
);


--
-- Name: TABLE mfa_challenges; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.mfa_challenges IS 'auth: stores metadata about challenge requests made';


--
-- Name: mfa_factors; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.mfa_factors (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    friendly_name text,
    factor_type auth.factor_type NOT NULL,
    status auth.factor_status NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    secret text,
    phone text,
    last_challenged_at timestamp with time zone,
    web_authn_credential jsonb,
    web_authn_aaguid uuid,
    last_webauthn_challenge_data jsonb
);


--
-- Name: TABLE mfa_factors; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.mfa_factors IS 'auth: stores metadata about factors';


--
-- Name: COLUMN mfa_factors.last_webauthn_challenge_data; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON COLUMN auth.mfa_factors.last_webauthn_challenge_data IS 'Stores the latest WebAuthn challenge data including attestation/assertion for customer verification';


--
-- Name: oauth_authorizations; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.oauth_authorizations (
    id uuid NOT NULL,
    authorization_id text NOT NULL,
    client_id uuid NOT NULL,
    user_id uuid,
    redirect_uri text NOT NULL,
    scope text NOT NULL,
    state text,
    resource text,
    code_challenge text,
    code_challenge_method auth.code_challenge_method,
    response_type auth.oauth_response_type DEFAULT 'code'::auth.oauth_response_type NOT NULL,
    status auth.oauth_authorization_status DEFAULT 'pending'::auth.oauth_authorization_status NOT NULL,
    authorization_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '00:03:00'::interval) NOT NULL,
    approved_at timestamp with time zone,
    nonce text,
    CONSTRAINT oauth_authorizations_authorization_code_length CHECK ((char_length(authorization_code) <= 255)),
    CONSTRAINT oauth_authorizations_code_challenge_length CHECK ((char_length(code_challenge) <= 128)),
    CONSTRAINT oauth_authorizations_expires_at_future CHECK ((expires_at > created_at)),
    CONSTRAINT oauth_authorizations_nonce_length CHECK ((char_length(nonce) <= 255)),
    CONSTRAINT oauth_authorizations_redirect_uri_length CHECK ((char_length(redirect_uri) <= 2048)),
    CONSTRAINT oauth_authorizations_resource_length CHECK ((char_length(resource) <= 2048)),
    CONSTRAINT oauth_authorizations_scope_length CHECK ((char_length(scope) <= 4096)),
    CONSTRAINT oauth_authorizations_state_length CHECK ((char_length(state) <= 4096))
);


--
-- Name: oauth_client_states; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.oauth_client_states (
    id uuid NOT NULL,
    provider_type text NOT NULL,
    code_verifier text,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: TABLE oauth_client_states; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.oauth_client_states IS 'Stores OAuth states for third-party provider authentication flows where Supabase acts as the OAuth client.';


--
-- Name: oauth_clients; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.oauth_clients (
    id uuid NOT NULL,
    client_secret_hash text,
    registration_type auth.oauth_registration_type NOT NULL,
    redirect_uris text NOT NULL,
    grant_types text NOT NULL,
    client_name text,
    client_uri text,
    logo_uri text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    client_type auth.oauth_client_type DEFAULT 'confidential'::auth.oauth_client_type NOT NULL,
    token_endpoint_auth_method text NOT NULL,
    CONSTRAINT oauth_clients_client_name_length CHECK ((char_length(client_name) <= 1024)),
    CONSTRAINT oauth_clients_client_uri_length CHECK ((char_length(client_uri) <= 2048)),
    CONSTRAINT oauth_clients_logo_uri_length CHECK ((char_length(logo_uri) <= 2048)),
    CONSTRAINT oauth_clients_token_endpoint_auth_method_check CHECK ((token_endpoint_auth_method = ANY (ARRAY['client_secret_basic'::text, 'client_secret_post'::text, 'none'::text])))
);


--
-- Name: oauth_consents; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.oauth_consents (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    client_id uuid NOT NULL,
    scopes text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT oauth_consents_revoked_after_granted CHECK (((revoked_at IS NULL) OR (revoked_at >= granted_at))),
    CONSTRAINT oauth_consents_scopes_length CHECK ((char_length(scopes) <= 2048)),
    CONSTRAINT oauth_consents_scopes_not_empty CHECK ((char_length(TRIM(BOTH FROM scopes)) > 0))
);


--
-- Name: one_time_tokens; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.one_time_tokens (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    token_type auth.one_time_token_type NOT NULL,
    token_hash text NOT NULL,
    relates_to text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT one_time_tokens_token_hash_check CHECK ((char_length(token_hash) > 0))
);


--
-- Name: refresh_tokens; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.refresh_tokens (
    instance_id uuid,
    id bigint NOT NULL,
    token character varying(255),
    user_id character varying(255),
    revoked boolean,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    parent character varying(255),
    session_id uuid
);


--
-- Name: TABLE refresh_tokens; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.refresh_tokens IS 'Auth: Store of tokens used to refresh JWT tokens once they expire.';


--
-- Name: refresh_tokens_id_seq; Type: SEQUENCE; Schema: auth; Owner: -
--

CREATE SEQUENCE auth.refresh_tokens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: refresh_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: auth; Owner: -
--

ALTER SEQUENCE auth.refresh_tokens_id_seq OWNED BY auth.refresh_tokens.id;


--
-- Name: saml_providers; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.saml_providers (
    id uuid NOT NULL,
    sso_provider_id uuid NOT NULL,
    entity_id text NOT NULL,
    metadata_xml text NOT NULL,
    metadata_url text,
    attribute_mapping jsonb,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    name_id_format text,
    CONSTRAINT "entity_id not empty" CHECK ((char_length(entity_id) > 0)),
    CONSTRAINT "metadata_url not empty" CHECK (((metadata_url = NULL::text) OR (char_length(metadata_url) > 0))),
    CONSTRAINT "metadata_xml not empty" CHECK ((char_length(metadata_xml) > 0))
);


--
-- Name: TABLE saml_providers; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.saml_providers IS 'Auth: Manages SAML Identity Provider connections.';


--
-- Name: saml_relay_states; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.saml_relay_states (
    id uuid NOT NULL,
    sso_provider_id uuid NOT NULL,
    request_id text NOT NULL,
    for_email text,
    redirect_to text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    flow_state_id uuid,
    CONSTRAINT "request_id not empty" CHECK ((char_length(request_id) > 0))
);


--
-- Name: TABLE saml_relay_states; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.saml_relay_states IS 'Auth: Contains SAML Relay State information for each Service Provider initiated login.';


--
-- Name: schema_migrations; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.schema_migrations (
    version character varying(255) NOT NULL
);


--
-- Name: TABLE schema_migrations; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.schema_migrations IS 'Auth: Manages updates to the auth system.';


--
-- Name: sessions; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.sessions (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    factor_id uuid,
    aal auth.aal_level,
    not_after timestamp with time zone,
    refreshed_at timestamp without time zone,
    user_agent text,
    ip inet,
    tag text,
    oauth_client_id uuid,
    refresh_token_hmac_key text,
    refresh_token_counter bigint,
    scopes text,
    CONSTRAINT sessions_scopes_length CHECK ((char_length(scopes) <= 4096))
);


--
-- Name: TABLE sessions; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.sessions IS 'Auth: Stores session data associated to a user.';


--
-- Name: COLUMN sessions.not_after; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON COLUMN auth.sessions.not_after IS 'Auth: Not after is a nullable column that contains a timestamp after which the session should be regarded as expired.';


--
-- Name: COLUMN sessions.refresh_token_hmac_key; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON COLUMN auth.sessions.refresh_token_hmac_key IS 'Holds a HMAC-SHA256 key used to sign refresh tokens for this session.';


--
-- Name: COLUMN sessions.refresh_token_counter; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON COLUMN auth.sessions.refresh_token_counter IS 'Holds the ID (counter) of the last issued refresh token.';


--
-- Name: sso_domains; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.sso_domains (
    id uuid NOT NULL,
    sso_provider_id uuid NOT NULL,
    domain text NOT NULL,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    CONSTRAINT "domain not empty" CHECK ((char_length(domain) > 0))
);


--
-- Name: TABLE sso_domains; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.sso_domains IS 'Auth: Manages SSO email address domain mapping to an SSO Identity Provider.';


--
-- Name: sso_providers; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.sso_providers (
    id uuid NOT NULL,
    resource_id text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    disabled boolean,
    CONSTRAINT "resource_id not empty" CHECK (((resource_id = NULL::text) OR (char_length(resource_id) > 0)))
);


--
-- Name: TABLE sso_providers; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.sso_providers IS 'Auth: Manages SSO identity provider information; see saml_providers for SAML.';


--
-- Name: COLUMN sso_providers.resource_id; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON COLUMN auth.sso_providers.resource_id IS 'Auth: Uniquely identifies a SSO provider according to a user-chosen resource ID (case insensitive), useful in infrastructure as code.';


--
-- Name: users; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.users (
    instance_id uuid,
    id uuid NOT NULL,
    aud character varying(255),
    role character varying(255),
    email character varying(255),
    encrypted_password character varying(255),
    email_confirmed_at timestamp with time zone,
    invited_at timestamp with time zone,
    confirmation_token character varying(255),
    confirmation_sent_at timestamp with time zone,
    recovery_token character varying(255),
    recovery_sent_at timestamp with time zone,
    email_change_token_new character varying(255),
    email_change character varying(255),
    email_change_sent_at timestamp with time zone,
    last_sign_in_at timestamp with time zone,
    raw_app_meta_data jsonb,
    raw_user_meta_data jsonb,
    is_super_admin boolean,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    phone text DEFAULT NULL::character varying,
    phone_confirmed_at timestamp with time zone,
    phone_change text DEFAULT ''::character varying,
    phone_change_token character varying(255) DEFAULT ''::character varying,
    phone_change_sent_at timestamp with time zone,
    confirmed_at timestamp with time zone GENERATED ALWAYS AS (LEAST(email_confirmed_at, phone_confirmed_at)) STORED,
    email_change_token_current character varying(255) DEFAULT ''::character varying,
    email_change_confirm_status smallint DEFAULT 0,
    banned_until timestamp with time zone,
    reauthentication_token character varying(255) DEFAULT ''::character varying,
    reauthentication_sent_at timestamp with time zone,
    is_sso_user boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    is_anonymous boolean DEFAULT false NOT NULL,
    CONSTRAINT users_email_change_confirm_status_check CHECK (((email_change_confirm_status >= 0) AND (email_change_confirm_status <= 2)))
);


--
-- Name: TABLE users; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON TABLE auth.users IS 'Auth: Stores user login data within a secure schema.';


--
-- Name: COLUMN users.is_sso_user; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON COLUMN auth.users.is_sso_user IS 'Auth: Set this column to true when the account comes from SSO. These accounts can have duplicate emails.';


--
-- Name: webauthn_challenges; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.webauthn_challenges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    challenge_type text NOT NULL,
    session_data jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT webauthn_challenges_challenge_type_check CHECK ((challenge_type = ANY (ARRAY['signup'::text, 'registration'::text, 'authentication'::text])))
);


--
-- Name: webauthn_credentials; Type: TABLE; Schema: auth; Owner: -
--

CREATE TABLE auth.webauthn_credentials (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    credential_id bytea NOT NULL,
    public_key bytea NOT NULL,
    attestation_type text DEFAULT ''::text NOT NULL,
    aaguid uuid,
    sign_count bigint DEFAULT 0 NOT NULL,
    transports jsonb DEFAULT '[]'::jsonb NOT NULL,
    backup_eligible boolean DEFAULT false NOT NULL,
    backed_up boolean DEFAULT false NOT NULL,
    friendly_name text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone
);


--
-- Name: agent_personas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_personas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key text NOT NULL,
    name text NOT NULL,
    title text,
    tagline text,
    style text,
    description text,
    key_principles jsonb,
    accent_color text,
    icon_name text,
    focus text,
    famous_quotes jsonb,
    persona_prompt text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE agent_personas; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_personas IS 'AI investor personas (Buffett, Wood, Lynch, Ackman, etc.) with system prompts';


--
-- Name: COLUMN agent_personas.key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_personas.key IS 'Snake_case identifier: warren_buffett, cathie_wood, etc.';


--
-- Name: COLUMN agent_personas.persona_prompt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_personas.persona_prompt IS 'System prompt sent to LLM for report generation';


--
-- Name: article_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.article_chunks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    article_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    chunk_text text NOT NULL,
    embedding public.vector(1536),
    section_title text,
    token_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: asset_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_snapshots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    symbol text NOT NULL,
    asset_type public.asset_type NOT NULL,
    snapshot_type text NOT NULL,
    title text,
    content jsonb NOT NULL,
    generated_by text,
    generated_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE asset_snapshots; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.asset_snapshots IS 'AI-generated analysis snapshots for ETFs, indexes, crypto, commodities. Refreshed weekly by Gemini.';


--
-- Name: COLUMN asset_snapshots.snapshot_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_snapshots.snapshot_type IS 'e.g. identity_rating, strategy, net_yield, holdings_risk (ETF); valuation, sector_performance, macro_forecast (Index)';


--
-- Name: COLUMN asset_snapshots.content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_snapshots.content IS 'JSONB: full snapshot payload. Schema varies by asset_type + snapshot_type.';


--
-- Name: COLUMN asset_snapshots.generated_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_snapshots.generated_by IS 'Model identifier, e.g. "Gemini 2.0 Flash"';


--
-- Name: book_chapters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.book_chapters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    book_id uuid NOT NULL,
    chapter_number integer NOT NULL,
    chapter_title text NOT NULL,
    sections jsonb NOT NULL,
    audio_duration_seconds integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: COLUMN book_chapters.sections; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.book_chapters.sections IS 'JSONB array: [{title, content, iconName?}]';


--
-- Name: book_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.book_chunks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    book_id uuid NOT NULL,
    chapter_number integer,
    chunk_index integer NOT NULL,
    chunk_text text NOT NULL,
    embedding public.vector(1536),
    section_title text,
    token_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: books; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.books (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    author text NOT NULL,
    description text,
    cover_image_name text,
    page_count integer,
    published_year integer,
    rating numeric,
    level public.book_level,
    is_most_read boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE books; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.books IS 'Investment education books available in the Learn section';


--
-- Name: chat_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    role public.chat_message_role NOT NULL,
    content text NOT NULL,
    rich_content jsonb,
    citations jsonb,
    tokens_used integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE chat_messages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.chat_messages IS 'Individual messages within a chat session';


--
-- Name: COLUMN chat_messages.rich_content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.chat_messages.rich_content IS 'JSONB array of typed blocks: text, sentimentAnalysis, stockPerformance, riskFactors, tip, bulletPoints';


--
-- Name: COLUMN chat_messages.citations; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.chat_messages.citations IS 'JSONB array: [{source, title, url?}]';


--
-- Name: chat_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    title text,
    session_type text DEFAULT 'NORMAL'::text NOT NULL,
    stock_id text,
    preview_message text,
    message_count integer DEFAULT 0 NOT NULL,
    is_saved boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_message_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE chat_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.chat_sessions IS 'AI chat sessions. Types: BOOK, CONCEPT, STOCK, NORMAL, JOURNEY, REPORT';


--
-- Name: COLUMN chat_sessions.stock_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.chat_sessions.stock_id IS 'Ticker symbol if chat is about a specific stock';


--
-- Name: company_filing_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_filing_chunks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    filing_type text NOT NULL,
    fiscal_year integer,
    fiscal_quarter integer,
    chunk_index integer NOT NULL,
    chunk_text text NOT NULL,
    embedding public.vector(1536),
    section_title text,
    token_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE company_filing_chunks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.company_filing_chunks IS 'Vectorized SEC filing chunks for RAG-based company research';


--
-- Name: company_profile_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_profile_cache (
    ticker text NOT NULL,
    profile_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: competitor_intel_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competitor_intel_audit (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    ticker text NOT NULL,
    status text NOT NULL,
    raw_response jsonb,
    suggested_tickers text[],
    validated_tickers text[],
    rejected jsonb,
    source_labels text[],
    tokens_used integer,
    model_version text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT competitor_intel_audit_status_check CHECK ((status = ANY (ARRAY['applied'::text, 'applied_with_rejections'::text, 'rejected_no_validated'::text, 'gemini_error'::text, 'skipped_kill_switch'::text])))
);


--
-- Name: competitor_intel_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.competitor_intel_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: competitor_intel_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.competitor_intel_audit_id_seq OWNED BY public.competitor_intel_audit.id;


--
-- Name: competitor_intel_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competitor_intel_cache (
    ticker text NOT NULL,
    competitor_tickers text[] NOT NULL,
    source_labels text[] DEFAULT '{}'::text[] NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    model_version text
);


--
-- Name: crypto_coin_id_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crypto_coin_id_cache (
    symbol text NOT NULL,
    coingecko_id text NOT NULL,
    name text,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: crypto_fundamentals_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crypto_fundamentals_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    symbol text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: crypto_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crypto_snapshots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    symbol text NOT NULL,
    category text NOT NULL,
    paragraphs jsonb NOT NULL,
    generated_at timestamp with time zone DEFAULT now(),
    generated_by text DEFAULT 'gemini-2.5-flash'::text
);


--
-- Name: daily_briefings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.daily_briefings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    type text DEFAULT 'wiser_trending'::text NOT NULL,
    title text NOT NULL,
    subtitle text NOT NULL,
    date timestamp with time zone,
    badge_text text,
    is_active boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT daily_briefings_type_check CHECK ((type = ANY (ARRAY['whales_alert'::text, 'earnings_alert'::text, 'whales_following'::text, 'wiser_trending'::text])))
);


--
-- Name: TABLE daily_briefings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.daily_briefings IS 'Configurable briefing cards for the Home screen daily briefings section';


--
-- Name: COLUMN daily_briefings.badge_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.daily_briefings.badge_text IS 'Optional date badge, e.g. "24\nFEB" for earnings dates';


--
-- Name: COLUMN daily_briefings.priority; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.daily_briefings.priority IS 'Higher priority items appear first (DESC order)';


--
-- Name: etf_detail_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.etf_detail_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    symbol text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: etf_snapshot_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.etf_snapshot_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    symbol text NOT NULL,
    category text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: geopolitical_macro_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.geopolitical_macro_audit (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    status text NOT NULL,
    factor_count integer,
    factors jsonb,
    raw_response jsonb,
    search_queries text[],
    tokens_used integer,
    model_version text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT geopolitical_macro_audit_status_check CHECK ((status = ANY (ARRAY['applied'::text, 'no_factors'::text, 'kept_last_good'::text, 'gemini_error'::text, 'skipped_kill_switch'::text])))
);


--
-- Name: geopolitical_macro_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.geopolitical_macro_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: geopolitical_macro_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.geopolitical_macro_audit_id_seq OWNED BY public.geopolitical_macro_audit.id;


--
-- Name: geopolitical_macro_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.geopolitical_macro_cache (
    scope text DEFAULT 'global'::text NOT NULL,
    factors jsonb DEFAULT '[]'::jsonb NOT NULL,
    model_version text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: health_check_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.health_check_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now(),
    next_earnings_date text
);


--
-- Name: hedge_fund_quarters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hedge_fund_quarters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    year integer NOT NULL,
    quarter integer NOT NULL,
    quarter_date text NOT NULL,
    buy_volume numeric(20,2) DEFAULT 0,
    sell_volume numeric(20,2) DEFAULT 0,
    net_flow numeric(20,2) DEFAULT 0,
    buyers_count integer DEFAULT 0,
    sellers_count integer DEFAULT 0,
    computed_at timestamp with time zone DEFAULT now(),
    CONSTRAINT hedge_fund_quarters_buy_volume_nonneg CHECK ((buy_volume >= (0)::numeric)),
    CONSTRAINT hedge_fund_quarters_buyers_count_nonneg CHECK ((buyers_count >= 0)),
    CONSTRAINT hedge_fund_quarters_quarter_range CHECK (((quarter >= 1) AND (quarter <= 4))),
    CONSTRAINT hedge_fund_quarters_sell_volume_nonneg CHECK ((sell_volume >= (0)::numeric)),
    CONSTRAINT hedge_fund_quarters_sellers_count_nonneg CHECK ((sellers_count >= 0))
);


--
-- Name: holders_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.holders_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: index_detail_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.index_detail_cache (
    cache_key text NOT NULL,
    symbol text NOT NULL,
    chart_range text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: index_macro_forecast_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.index_macro_forecast_cache (
    symbol text NOT NULL,
    story_template text NOT NULL,
    indicators_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: industry_dossier; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.industry_dossier (
    id bigint NOT NULL,
    industry text NOT NULL,
    sector text NOT NULL,
    current_tam_b numeric(14,2),
    future_tam_b numeric(14,2),
    current_year text,
    future_year text,
    cagr_5y_pct numeric(8,4),
    lifecycle_phase text DEFAULT 'mature'::text NOT NULL,
    hhi numeric(10,2),
    top1_share_pct numeric(6,2),
    top2_share_pct numeric(6,2),
    concentration_label text,
    constituent_count integer,
    source_grain text NOT NULL,
    source_label text NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '8 days'::interval) NOT NULL,
    tam_scope text DEFAULT 'us'::text NOT NULL,
    CONSTRAINT industry_dossier_concentration_label_check CHECK ((concentration_label = ANY (ARRAY['monopoly'::text, 'duopoly'::text, 'oligopoly'::text, 'fragmented'::text]))),
    CONSTRAINT industry_dossier_lifecycle_phase_check CHECK ((lifecycle_phase = ANY (ARRAY['emerging'::text, 'secular_growth'::text, 'mature'::text, 'declining'::text]))),
    CONSTRAINT industry_dossier_source_grain_check CHECK ((source_grain = ANY (ARRAY['industry'::text, 'sector'::text, 'all_industry'::text]))),
    CONSTRAINT industry_dossier_tam_scope_check CHECK ((tam_scope = ANY (ARRAY['us'::text, 'global'::text])))
);


--
-- Name: industry_dossier_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.industry_dossier_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: industry_dossier_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.industry_dossier_id_seq OWNED BY public.industry_dossier.id;


--
-- Name: industry_moat_benchmarks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.industry_moat_benchmarks (
    id bigint NOT NULL,
    industry text NOT NULL,
    pillar_name text NOT NULL,
    peer_average_score numeric(3,1) NOT NULL,
    sample_size integer NOT NULL,
    score_p25 numeric(3,1),
    score_p75 numeric(3,1),
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    model_version text
);


--
-- Name: industry_moat_benchmarks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.industry_moat_benchmarks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: industry_moat_benchmarks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.industry_moat_benchmarks_id_seq OWNED BY public.industry_moat_benchmarks.id;


--
-- Name: industry_override_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.industry_override_audit (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    industry text NOT NULL,
    sector text NOT NULL,
    status text NOT NULL,
    raw_response jsonb,
    phase_a_tam_b numeric(14,2),
    applied_tam_b numeric(14,2),
    applied_cagr_pct numeric(8,4),
    applied_source_label text,
    rejection_reason text,
    tokens_used integer,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    model_version text,
    CONSTRAINT industry_override_audit_status_check CHECK ((status = ANY (ARRAY['applied'::text, 'applied_with_warning'::text, 'rejected_validation'::text, 'rejected_sanity'::text, 'rejected_low_confidence'::text, 'rejected_below_phase_a'::text, 'gemini_error'::text, 'skipped_kill_switch'::text])))
);


--
-- Name: COLUMN industry_override_audit.model_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.industry_override_audit.model_version IS 'Gemini model identifier captured from the API response (e.g. gemini-1.5-pro). Null for rows written before migration 052 and for skipped_kill_switch rows.';


--
-- Name: industry_override_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.industry_override_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: industry_override_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.industry_override_audit_id_seq OWNED BY public.industry_override_audit.id;


--
-- Name: ip_intel_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ip_intel_audit (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    ticker text NOT NULL,
    status text NOT NULL,
    payload jsonb,
    uspto_total integer,
    uspto_recent_5y integer,
    fda_active integer,
    assignee_name text,
    sponsor_name text,
    error_detail text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ip_intel_audit_status_check CHECK ((status = ANY (ARRAY['applied'::text, 'applied_partial'::text, 'rejected_no_data'::text, 'uspto_error'::text, 'fda_error'::text, 'skipped'::text])))
);


--
-- Name: ip_intel_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ip_intel_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ip_intel_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ip_intel_audit_id_seq OWNED BY public.ip_intel_audit.id;


--
-- Name: ip_intel_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ip_intel_cache (
    ticker text NOT NULL,
    payload jsonb NOT NULL,
    source_labels text[] DEFAULT '{}'::text[] NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: lessons; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lessons (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    description text,
    duration_minutes integer,
    category text DEFAULT 'standard'::text NOT NULL,
    level public.lesson_level NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    story_content jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE lessons; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lessons IS 'Investor journey lessons organized by level';


--
-- Name: COLUMN lessons.story_content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.lessons.story_content IS 'JSONB: {lessonLabel, lessonNumber, totalLessonsInLevel, estimatedMinutes, cards[]}';


--
-- Name: market_deep_dive_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_deep_dive_cache (
    symbol text NOT NULL,
    context_hash text NOT NULL,
    report_markdown text NOT NULL,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: market_insights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_insights (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    headline text NOT NULL,
    bullet_points jsonb DEFAULT '[]'::jsonb NOT NULL,
    sentiment text DEFAULT 'Neutral'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT market_insights_sentiment_check CHECK ((sentiment = ANY (ARRAY['Bullish'::text, 'Bearish'::text, 'Neutral'::text])))
);


--
-- Name: TABLE market_insights; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.market_insights IS 'AI-generated market summaries for the Home screen insight card';


--
-- Name: COLUMN market_insights.bullet_points; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.market_insights.bullet_points IS 'JSONB array of strings: ["point 1", "point 2"]';


--
-- Name: COLUMN market_insights.sentiment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.market_insights.sentiment IS 'Overall market sentiment: Bullish, Bearish, or Neutral';


--
-- Name: moat_intel_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.moat_intel_audit (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    ticker text NOT NULL,
    status text NOT NULL,
    raw_response jsonb,
    pillars_requested text[],
    pillars_resolved text[],
    rejected jsonb,
    source_labels text[],
    tokens_used integer,
    model_version text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT moat_intel_audit_status_check CHECK ((status = ANY (ARRAY['applied'::text, 'applied_with_rejections'::text, 'rejected_no_validated'::text, 'gemini_error'::text, 'skipped_kill_switch'::text])))
);


--
-- Name: moat_intel_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.moat_intel_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: moat_intel_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.moat_intel_audit_id_seq OWNED BY public.moat_intel_audit.id;


--
-- Name: moat_intel_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.moat_intel_cache (
    ticker text NOT NULL,
    pillar_scores jsonb NOT NULL,
    source_labels text[] DEFAULT '{}'::text[] NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    model_version text
);


--
-- Name: money_move_articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.money_move_articles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    subtitle text,
    category public.money_move_category NOT NULL,
    author_name text,
    author_credentials text,
    author_avatar_name text,
    published_at timestamp with time zone,
    read_time_minutes integer,
    sections jsonb,
    statistics jsonb,
    related_articles jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    slug text,
    content jsonb,
    view_count text,
    is_featured boolean DEFAULT false NOT NULL,
    has_audio_version boolean DEFAULT false NOT NULL,
    audio_url text,
    audio_duration_seconds integer,
    sort_order integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE money_move_articles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.money_move_articles IS 'Investment case studies: blueprints, value traps, and battles';


--
-- Name: COLUMN money_move_articles.sections; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.money_move_articles.sections IS 'JSONB array: [{type, title?, content?, items?[], imageURL?}]';


--
-- Name: COLUMN money_move_articles.content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.money_move_articles.content IS 'JSONB passthrough of the full iOS MoneyMoveArticleDTO (camelCase keys). Source of truth for the served article.';


--
-- Name: COLUMN money_move_articles.audio_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.money_move_articles.audio_url IS 'Public money-moves-media URL of the narration .m4a (Achird TTS). NULL until voice is generated.';


--
-- Name: news_articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_articles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    headline text NOT NULL,
    summary text,
    source_name text NOT NULL,
    source_logo_url text,
    source_is_verified boolean DEFAULT false NOT NULL,
    sentiment public.news_sentiment,
    published_at timestamp with time zone NOT NULL,
    thumbnail_url text,
    related_tickers jsonb,
    category text,
    is_breaking boolean DEFAULT false NOT NULL,
    article_url text,
    insight_summary text,
    insight_key_points jsonb,
    key_takeaways jsonb,
    read_time_minutes integer,
    external_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE news_articles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.news_articles IS 'Aggregated news articles with AI-enriched insights';


--
-- Name: COLUMN news_articles.related_tickers; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_articles.related_tickers IS 'JSONB array of ticker strings: ["AAPL", "MSFT"]';


--
-- Name: COLUMN news_articles.key_takeaways; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_articles.key_takeaways IS 'JSONB array: [{index, text}]';


--
-- Name: portfolio_holdings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolio_holdings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    ticker text NOT NULL,
    company_name text NOT NULL,
    market_value numeric(18,2) DEFAULT 0 NOT NULL,
    sector text,
    asset_type text DEFAULT 'Stock'::text NOT NULL,
    country text DEFAULT 'US'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    shares numeric(20,4),
    CONSTRAINT portfolio_holdings_market_value_nonneg CHECK ((market_value >= (0)::numeric)),
    CONSTRAINT portfolio_holdings_shares_nonneg CHECK (((shares IS NULL) OR (shares >= (0)::numeric)))
);


--
-- Name: COLUMN portfolio_holdings.shares; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.portfolio_holdings.shares IS 'Share count. When set, market_value is recomputed from FMP live price on read.';


--
-- Name: portfolio_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolio_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    portfolio_id uuid NOT NULL,
    ticker text NOT NULL,
    "position" integer DEFAULT 0 NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    shares numeric(20,4),
    market_value numeric(20,2),
    CONSTRAINT portfolio_items_market_value_nonneg CHECK (((market_value IS NULL) OR (market_value >= (0)::numeric))),
    CONSTRAINT portfolio_items_shares_nonneg CHECK (((shares IS NULL) OR (shares >= (0)::numeric)))
);


--
-- Name: portfolios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolios (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: price_catalyst_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_catalyst_audit (
    id bigint NOT NULL,
    run_id uuid NOT NULL,
    ticker text NOT NULL,
    status text NOT NULL,
    change_pct double precision,
    window_label text,
    tag text,
    reason text,
    sources jsonb,
    raw_response jsonb,
    search_queries text[],
    tokens_used integer,
    model_version text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT price_catalyst_audit_status_check CHECK ((status = ANY (ARRAY['applied'::text, 'no_catalyst'::text, 'gemini_error'::text, 'skipped_kill_switch'::text])))
);


--
-- Name: price_catalyst_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.price_catalyst_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: price_catalyst_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.price_catalyst_audit_id_seq OWNED BY public.price_catalyst_audit.id;


--
-- Name: price_catalyst_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_catalyst_cache (
    ticker text NOT NULL,
    tag text,
    reason text,
    sources jsonb DEFAULT '[]'::jsonb NOT NULL,
    model_version text,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: profit_power_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.profit_power_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now(),
    next_earnings_date text
);


--
-- Name: research_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.research_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    ticker text NOT NULL,
    company_name text NOT NULL,
    investor_persona text NOT NULL,
    status public.report_status DEFAULT 'pending'::public.report_status NOT NULL,
    progress integer DEFAULT 0 NOT NULL,
    current_step text,
    error_message text,
    estimated_time_remaining integer,
    title text,
    executive_summary text,
    investment_thesis jsonb,
    pros jsonb,
    cons jsonb,
    moat_analysis jsonb,
    valuation_analysis jsonb,
    risk_assessment jsonb,
    full_report text,
    key_takeaways jsonb,
    action_recommendation text,
    generation_time_seconds integer,
    tokens_used integer,
    user_rating integer,
    user_feedback text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    overall_score numeric,
    fair_value_estimate numeric,
    ticker_report_data jsonb,
    industry text,
    is_refunded boolean DEFAULT false NOT NULL,
    credits_charged integer DEFAULT 5 NOT NULL,
    pdf_path text,
    pdf_status text DEFAULT 'pending'::text,
    pdf_generated_at timestamp with time zone,
    processing_started_at timestamp with time zone,
    CONSTRAINT research_reports_fair_value_nonneg CHECK (((fair_value_estimate IS NULL) OR (fair_value_estimate >= (0)::numeric))),
    CONSTRAINT research_reports_overall_score_check CHECK (((overall_score >= (0)::numeric) AND (overall_score <= (100)::numeric))),
    CONSTRAINT research_reports_progress_check CHECK (((progress >= 0) AND (progress <= 100))),
    CONSTRAINT research_reports_user_rating_check CHECK (((user_rating >= 1) AND (user_rating <= 5)))
);


--
-- Name: TABLE research_reports; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.research_reports IS 'AI research reports. Dual-purpose: task queue + content store.';


--
-- Name: COLUMN research_reports.investment_thesis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.research_reports.investment_thesis IS 'JSONB: {summary, key_drivers[], risks[], time_horizon, conviction_level}';


--
-- Name: COLUMN research_reports.moat_analysis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.research_reports.moat_analysis IS 'JSONB: {moat_rating, moat_sources[], moat_sustainability, competitive_position, barriers_to_entry[]}';


--
-- Name: COLUMN research_reports.valuation_analysis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.research_reports.valuation_analysis IS 'JSONB: {valuation_rating, key_metrics{}, historical_context, margin_of_safety}';


--
-- Name: COLUMN research_reports.risk_assessment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.research_reports.risk_assessment IS 'JSONB: {overall_risk, business_risks[], financial_risks[], market_risks[]}';


--
-- Name: COLUMN research_reports.overall_score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.research_reports.overall_score IS 'AI-generated quality score 0-100 (used in home feed & report cards)';


--
-- Name: COLUMN research_reports.fair_value_estimate; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.research_reports.fair_value_estimate IS 'Estimated fair value per share (used in home feed & report cards)';


--
-- Name: COLUMN research_reports.ticker_report_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.research_reports.ticker_report_data IS 'Full TickerReportResponse JSON from multi-agent research. Matches the exact schema expected by iOS TickerReportView.';


--
-- Name: revenue_breakdown_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.revenue_breakdown_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now(),
    next_earnings_date text
);


--
-- Name: sector_aggregates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sector_aggregates (
    sector text NOT NULL,
    total_revenue_usd numeric(24,2),
    cagr_5yr_pct numeric(9,4),
    hhi numeric(10,4),
    top1_share_pct numeric(7,4),
    top2_share_pct numeric(7,4),
    num_constituents integer,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sector_aggregates_top1_share_pct_range CHECK (((top1_share_pct IS NULL) OR ((top1_share_pct >= (0)::numeric) AND (top1_share_pct <= (100)::numeric)))),
    CONSTRAINT sector_aggregates_top2_share_pct_range CHECK (((top2_share_pct IS NULL) OR ((top2_share_pct >= (0)::numeric) AND (top2_share_pct <= (100)::numeric))))
);


--
-- Name: sector_benchmarks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sector_benchmarks (
    id bigint NOT NULL,
    sector text NOT NULL,
    metric_name text NOT NULL,
    period_type text NOT NULL,
    period_label text NOT NULL,
    median_value numeric(20,6) NOT NULL,
    sample_size integer DEFAULT 0 NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    industry text DEFAULT ''::text NOT NULL,
    CONSTRAINT sector_benchmarks_period_type_check CHECK ((period_type = ANY (ARRAY['annual'::text, 'quarterly'::text, 'ttm'::text]))),
    CONSTRAINT sector_benchmarks_sample_size_nonneg CHECK ((sample_size >= 0))
);


--
-- Name: sector_benchmarks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sector_benchmarks ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.sector_benchmarks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: short_interest_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.short_interest_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: signal_of_confidence_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_of_confidence_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now(),
    next_earnings_date text
);


--
-- Name: signals_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signals_cache (
    id bigint NOT NULL,
    cache_key text NOT NULL,
    data jsonb NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: signals_cache_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signals_cache_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signals_cache_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signals_cache_id_seq OWNED BY public.signals_cache.id;


--
-- Name: snapshot_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.snapshot_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    category text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: social_mentions_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_mentions_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    mentions integer DEFAULT 0 NOT NULL,
    upvotes integer DEFAULT 0 NOT NULL,
    rank integer,
    source text DEFAULT 'apewisdom'::text,
    snapshot_date date NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: stock_fundamentals_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_fundamentals_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    response_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now()
);


--
-- Name: ticker_data_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ticker_data_cache (
    ticker text NOT NULL,
    collected_data jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ticker_news_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ticker_news_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticker text NOT NULL,
    external_id text,
    headline text NOT NULL,
    summary text,
    summary_bullets jsonb DEFAULT '[]'::jsonb,
    sentiment text,
    sentiment_confidence integer DEFAULT 0,
    source_name text,
    source_logo_url text,
    published_at timestamp with time zone,
    thumbnail_url text,
    article_url text,
    related_tickers jsonb DEFAULT '[]'::jsonb,
    ai_processed boolean DEFAULT false,
    ai_model text,
    cached_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone DEFAULT (now() + '06:00:00'::interval),
    CONSTRAINT ticker_news_cache_sentiment_check CHECK ((sentiment = ANY (ARRAY['bullish'::text, 'bearish'::text, 'neutral'::text, 'Positive'::text, 'Negative'::text])))
);


--
-- Name: ticker_report_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ticker_report_cache (
    ticker text NOT NULL,
    persona text NOT NULL,
    ticker_report_data jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_book_progress; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_book_progress (
    id bigint NOT NULL,
    user_id uuid NOT NULL,
    curriculum_order integer NOT NULL,
    core_number integer NOT NULL,
    completed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_book_progress_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_book_progress_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_book_progress_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_book_progress_id_seq OWNED BY public.user_book_progress.id;


--
-- Name: user_bookmarks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_bookmarks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    bookmarkable_type public.bookmark_type NOT NULL,
    bookmarkable_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE user_bookmarks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_bookmarks IS 'Polymorphic bookmarks: book, lesson, article, or report';


--
-- Name: user_credits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_credits (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    total integer DEFAULT 0 NOT NULL,
    used integer DEFAULT 0 NOT NULL,
    remaining integer GENERATED ALWAYS AS ((total - used)) STORED,
    resets_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_credits_total_nonneg CHECK ((total >= 0)),
    CONSTRAINT user_credits_used_nonneg CHECK ((used >= 0))
);


--
-- Name: TABLE user_credits; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_credits IS 'Per-user credit balance for AI research generation';


--
-- Name: COLUMN user_credits.remaining; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_credits.remaining IS 'Auto-computed: total - used. Never set directly.';


--
-- Name: user_learn_progress; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_learn_progress (
    id bigint NOT NULL,
    user_id uuid NOT NULL,
    content_type text NOT NULL,
    item_key text NOT NULL,
    completed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_learn_progress_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_learn_progress_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_learn_progress_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_learn_progress_id_seq OWNED BY public.user_learn_progress.id;


--
-- Name: user_lesson_progress; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_lesson_progress (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    lesson_id uuid NOT NULL,
    status public.lesson_status DEFAULT 'notStarted'::public.lesson_status NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: user_study_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_study_schedules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    daily_reminder_enabled boolean DEFAULT false NOT NULL,
    morning_session_time text,
    review_time text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE user_study_schedules; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_study_schedules IS 'User learning schedule preferences';


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    email text NOT NULL,
    display_name text,
    avatar_url text,
    tier public.user_tier DEFAULT 'free'::public.user_tier NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.users IS 'Core user profiles. id = auth.users.id (direct link).';


--
-- Name: COLUMN users.tier; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.tier IS 'Subscription tier: free (default), pro, premium';


--
-- Name: vector_search_stats; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vector_search_stats AS
 SELECT 'book_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (book_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(book_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT book_chunks.book_id) AS unique_sources
   FROM public.book_chunks
UNION ALL
 SELECT 'article_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (article_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(article_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT article_chunks.article_id) AS unique_sources
   FROM public.article_chunks
UNION ALL
 SELECT 'company_filing_chunks'::text AS table_name,
    count(*) AS total_vectors,
    count(*) FILTER (WHERE (company_filing_chunks.embedding IS NOT NULL)) AS indexed_vectors,
    COALESCE(avg(company_filing_chunks.token_count), (0)::numeric) AS avg_tokens,
    count(DISTINCT company_filing_chunks.ticker) AS unique_sources
   FROM public.company_filing_chunks;


--
-- Name: watchlist_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlist_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    ticker text NOT NULL,
    company_name text NOT NULL,
    logo_url text,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    shares numeric(20,4),
    market_value numeric(20,2),
    sector text,
    asset_type text DEFAULT 'Stock'::text,
    country text DEFAULT 'US'::text,
    industry text,
    market_cap numeric(24,2),
    beta numeric(10,4),
    CONSTRAINT watchlist_items_market_value_nonneg CHECK (((market_value IS NULL) OR (market_value >= (0)::numeric))),
    CONSTRAINT watchlist_items_shares_nonneg CHECK (((shares IS NULL) OR (shares >= (0)::numeric)))
);


--
-- Name: TABLE watchlist_items; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.watchlist_items IS 'User watchlist. Price data fetched live from FMP API.';


--
-- Name: COLUMN watchlist_items.shares; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.watchlist_items.shares IS 'Optional share count. When set, market_value is recomputed from FMP live price on read.';


--
-- Name: COLUMN watchlist_items.market_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.watchlist_items.market_value IS 'Optional dollar amount. Used as the holding value when shares is null, or as the cached value otherwise.';


--
-- Name: whale_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    description text NOT NULL,
    ticker text,
    action_title text DEFAULT 'View Full Alert'::text NOT NULL,
    whale_id uuid,
    is_active boolean DEFAULT true NOT NULL,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE whale_alerts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.whale_alerts IS 'Whale activity alert banners for the Whales tab';


--
-- Name: whale_filing_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_filing_snapshots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    whale_id uuid NOT NULL,
    filing_period text NOT NULL,
    filing_date text NOT NULL,
    total_value numeric(24,2),
    holdings_data jsonb DEFAULT '[]'::jsonb NOT NULL,
    sector_data jsonb DEFAULT '[]'::jsonb NOT NULL,
    trade_group jsonb,
    behavior_summary jsonb,
    sentiment_text text,
    raw_hash text,
    processed_at timestamp with time zone DEFAULT now() NOT NULL,
    logo_cache jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: TABLE whale_filing_snapshots; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.whale_filing_snapshots IS 'Cached aggregated 13F/congressional data per whale per period';


--
-- Name: COLUMN whale_filing_snapshots.filing_period; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whale_filing_snapshots.filing_period IS 'e.g. "2025-Q4" (13F) or "2026-02" (congressional monthly)';


--
-- Name: COLUMN whale_filing_snapshots.holdings_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whale_filing_snapshots.holdings_data IS 'JSONB array: [{ticker, companyName, shares, value, allocation}]';


--
-- Name: COLUMN whale_filing_snapshots.trade_group; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whale_filing_snapshots.trade_group IS 'JSONB: {tradeCount, netAction, netAmount, summary, insights[], trades[]}';


--
-- Name: COLUMN whale_filing_snapshots.raw_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whale_filing_snapshots.raw_hash IS 'SHA256 of raw FMP response for change detection';


--
-- Name: COLUMN whale_filing_snapshots.logo_cache; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whale_filing_snapshots.logo_cache IS 'Cached {ticker: logo_url} from FMP company profiles';


--
-- Name: whale_follows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_follows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    whale_id uuid NOT NULL,
    followed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: whale_holdings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_holdings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    whale_id uuid NOT NULL,
    ticker text NOT NULL,
    company_name text NOT NULL,
    logo_url text,
    allocation numeric(7,4) NOT NULL,
    change_percent numeric(9,4),
    CONSTRAINT whale_holdings_allocation_range CHECK (((allocation >= (0)::numeric) AND (allocation <= (100)::numeric)))
);


--
-- Name: whale_profile_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_profile_cache (
    whale_id uuid NOT NULL,
    profile_json jsonb NOT NULL,
    cached_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE whale_profile_cache; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.whale_profile_cache IS 'Cache-aside for assembled WhaleProfileResponse JSON. 24h TTL.';


--
-- Name: whale_sector_allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_sector_allocations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    whale_id uuid NOT NULL,
    sector text NOT NULL,
    allocation numeric(7,4) NOT NULL,
    CONSTRAINT whale_sector_allocations_allocation_range CHECK (((allocation >= (0)::numeric) AND (allocation <= (100)::numeric)))
);


--
-- Name: whale_trade_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_trade_groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    whale_id uuid NOT NULL,
    date text NOT NULL,
    trade_count integer NOT NULL,
    net_action text NOT NULL,
    net_amount numeric(24,2) NOT NULL,
    summary text,
    insights jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT whale_trade_groups_net_amount_nonneg CHECK ((net_amount >= (0)::numeric))
);


--
-- Name: COLUMN whale_trade_groups.insights; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whale_trade_groups.insights IS 'JSONB array of insight strings';


--
-- Name: whale_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whale_trades (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    whale_id uuid NOT NULL,
    trade_group_id uuid,
    ticker text NOT NULL,
    company_name text NOT NULL,
    action public.trade_action NOT NULL,
    trade_type public.trade_type NOT NULL,
    amount numeric(24,2) NOT NULL,
    previous_allocation numeric(7,4),
    new_allocation numeric(7,4),
    date text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    amount_range text,
    disclosure_date text,
    CONSTRAINT whale_trades_amount_nonneg CHECK ((amount >= (0)::numeric))
);


--
-- Name: whales; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whales (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    title text,
    description text,
    avatar_url text,
    category public.whale_category DEFAULT 'investors'::public.whale_category NOT NULL,
    risk_profile public.whale_risk_profile,
    portfolio_value numeric(24,2),
    ytd_return numeric(9,4),
    followers_count integer DEFAULT 0 NOT NULL,
    behavior_summary jsonb,
    sentiment_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    cik text,
    data_source text DEFAULT 'manual'::text NOT NULL,
    fmp_name text,
    last_hydrated_at timestamp with time zone,
    associated_ticker text,
    return_source text DEFAULT ''::text,
    return_label text DEFAULT ''::text,
    CONSTRAINT whales_followers_count_nonneg CHECK ((followers_count >= 0)),
    CONSTRAINT whales_portfolio_value_nonneg CHECK (((portfolio_value IS NULL) OR (portfolio_value >= (0)::numeric)))
);


--
-- Name: TABLE whales; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.whales IS 'Notable investors, institutions, and politicians tracked for trades';


--
-- Name: COLUMN whales.behavior_summary; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whales.behavior_summary IS 'JSONB: {action, primaryFocus, secondaryAction, secondaryFocus}';


--
-- Name: COLUMN whales.cik; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whales.cik IS 'SEC Central Index Key for 13F institutional filers';


--
-- Name: COLUMN whales.data_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whales.data_source IS 'Routing: 13f | congressional_house | congressional_senate | manual';


--
-- Name: COLUMN whales.fmp_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whales.fmp_name IS 'Exact name for FMP congressional trade lookups';


--
-- Name: COLUMN whales.last_hydrated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.whales.last_hydrated_at IS 'Timestamp of last successful hydration run';


--
-- Name: messages; Type: TABLE; Schema: realtime; Owner: -
--

CREATE TABLE realtime.messages (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    inserted_at timestamp without time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    binary_payload bytea
)
PARTITION BY RANGE (inserted_at);


--
-- Name: schema_migrations; Type: TABLE; Schema: realtime; Owner: -
--

CREATE TABLE realtime.schema_migrations (
    version bigint NOT NULL,
    inserted_at timestamp(0) without time zone
);


--
-- Name: subscription; Type: TABLE; Schema: realtime; Owner: -
--

CREATE TABLE realtime.subscription (
    id bigint NOT NULL,
    subscription_id uuid NOT NULL,
    entity regclass NOT NULL,
    filters realtime.user_defined_filter[] DEFAULT '{}'::realtime.user_defined_filter[] NOT NULL,
    claims jsonb NOT NULL,
    claims_role regrole GENERATED ALWAYS AS (realtime.to_regrole((claims ->> 'role'::text))) STORED NOT NULL,
    created_at timestamp without time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    action_filter text DEFAULT '*'::text,
    selected_columns text[],
    CONSTRAINT subscription_action_filter_check CHECK ((action_filter = ANY (ARRAY['*'::text, 'INSERT'::text, 'UPDATE'::text, 'DELETE'::text])))
);


--
-- Name: subscription_id_seq; Type: SEQUENCE; Schema: realtime; Owner: -
--

ALTER TABLE realtime.subscription ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME realtime.subscription_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: buckets; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.buckets (
    id text NOT NULL,
    name text NOT NULL,
    owner uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    public boolean DEFAULT false,
    avif_autodetection boolean DEFAULT false,
    file_size_limit bigint,
    allowed_mime_types text[],
    owner_id text,
    type storage.buckettype DEFAULT 'STANDARD'::storage.buckettype NOT NULL
);


--
-- Name: COLUMN buckets.owner; Type: COMMENT; Schema: storage; Owner: -
--

COMMENT ON COLUMN storage.buckets.owner IS 'Field is deprecated, use owner_id instead';


--
-- Name: buckets_analytics; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.buckets_analytics (
    name text NOT NULL,
    type storage.buckettype DEFAULT 'ANALYTICS'::storage.buckettype NOT NULL,
    format text DEFAULT 'ICEBERG'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: buckets_vectors; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.buckets_vectors (
    id text NOT NULL,
    type storage.buckettype DEFAULT 'VECTOR'::storage.buckettype NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: migrations; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.migrations (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    hash character varying(40) NOT NULL,
    executed_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: objects; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.objects (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bucket_id text,
    name text,
    owner uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    last_accessed_at timestamp with time zone DEFAULT now(),
    metadata jsonb,
    path_tokens text[] GENERATED ALWAYS AS (string_to_array(name, '/'::text)) STORED,
    version text,
    owner_id text,
    user_metadata jsonb
);


--
-- Name: COLUMN objects.owner; Type: COMMENT; Schema: storage; Owner: -
--

COMMENT ON COLUMN storage.objects.owner IS 'Field is deprecated, use owner_id instead';


--
-- Name: s3_multipart_uploads; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.s3_multipart_uploads (
    id text NOT NULL,
    in_progress_size bigint DEFAULT 0 NOT NULL,
    upload_signature text NOT NULL,
    bucket_id text NOT NULL,
    key text NOT NULL COLLATE pg_catalog."C",
    version text NOT NULL,
    owner_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    user_metadata jsonb,
    metadata jsonb
);


--
-- Name: s3_multipart_uploads_parts; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.s3_multipart_uploads_parts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    upload_id text NOT NULL,
    size bigint DEFAULT 0 NOT NULL,
    part_number integer NOT NULL,
    bucket_id text NOT NULL,
    key text NOT NULL COLLATE pg_catalog."C",
    etag text NOT NULL,
    owner_id text,
    version text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vector_indexes; Type: TABLE; Schema: storage; Owner: -
--

CREATE TABLE storage.vector_indexes (
    id text DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL COLLATE pg_catalog."C",
    bucket_id text NOT NULL,
    data_type text NOT NULL,
    dimension integer NOT NULL,
    distance_metric text NOT NULL,
    metadata_configuration jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_migrations; Type: TABLE; Schema: supabase_migrations; Owner: -
--

CREATE TABLE supabase_migrations.schema_migrations (
    version text NOT NULL,
    statements text[],
    name text
);


--
-- Name: refresh_tokens id; Type: DEFAULT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.refresh_tokens ALTER COLUMN id SET DEFAULT nextval('auth.refresh_tokens_id_seq'::regclass);


--
-- Name: competitor_intel_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitor_intel_audit ALTER COLUMN id SET DEFAULT nextval('public.competitor_intel_audit_id_seq'::regclass);


--
-- Name: geopolitical_macro_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.geopolitical_macro_audit ALTER COLUMN id SET DEFAULT nextval('public.geopolitical_macro_audit_id_seq'::regclass);


--
-- Name: industry_dossier id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_dossier ALTER COLUMN id SET DEFAULT nextval('public.industry_dossier_id_seq'::regclass);


--
-- Name: industry_moat_benchmarks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_moat_benchmarks ALTER COLUMN id SET DEFAULT nextval('public.industry_moat_benchmarks_id_seq'::regclass);


--
-- Name: industry_override_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_override_audit ALTER COLUMN id SET DEFAULT nextval('public.industry_override_audit_id_seq'::regclass);


--
-- Name: ip_intel_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ip_intel_audit ALTER COLUMN id SET DEFAULT nextval('public.ip_intel_audit_id_seq'::regclass);


--
-- Name: moat_intel_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.moat_intel_audit ALTER COLUMN id SET DEFAULT nextval('public.moat_intel_audit_id_seq'::regclass);


--
-- Name: price_catalyst_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_catalyst_audit ALTER COLUMN id SET DEFAULT nextval('public.price_catalyst_audit_id_seq'::regclass);


--
-- Name: signals_cache id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signals_cache ALTER COLUMN id SET DEFAULT nextval('public.signals_cache_id_seq'::regclass);


--
-- Name: user_book_progress id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_book_progress ALTER COLUMN id SET DEFAULT nextval('public.user_book_progress_id_seq'::regclass);


--
-- Name: user_learn_progress id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_learn_progress ALTER COLUMN id SET DEFAULT nextval('public.user_learn_progress_id_seq'::regclass);


--
-- Name: mfa_amr_claims amr_id_pk; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_amr_claims
    ADD CONSTRAINT amr_id_pk PRIMARY KEY (id);


--
-- Name: audit_log_entries audit_log_entries_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.audit_log_entries
    ADD CONSTRAINT audit_log_entries_pkey PRIMARY KEY (id);


--
-- Name: custom_oauth_providers custom_oauth_providers_identifier_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.custom_oauth_providers
    ADD CONSTRAINT custom_oauth_providers_identifier_key UNIQUE (identifier);


--
-- Name: custom_oauth_providers custom_oauth_providers_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.custom_oauth_providers
    ADD CONSTRAINT custom_oauth_providers_pkey PRIMARY KEY (id);


--
-- Name: flow_state flow_state_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.flow_state
    ADD CONSTRAINT flow_state_pkey PRIMARY KEY (id);


--
-- Name: identities identities_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.identities
    ADD CONSTRAINT identities_pkey PRIMARY KEY (id);


--
-- Name: identities identities_provider_id_provider_unique; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.identities
    ADD CONSTRAINT identities_provider_id_provider_unique UNIQUE (provider_id, provider);


--
-- Name: instances instances_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.instances
    ADD CONSTRAINT instances_pkey PRIMARY KEY (id);


--
-- Name: mfa_amr_claims mfa_amr_claims_session_id_authentication_method_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_amr_claims
    ADD CONSTRAINT mfa_amr_claims_session_id_authentication_method_pkey UNIQUE (session_id, authentication_method);


--
-- Name: mfa_challenges mfa_challenges_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_challenges
    ADD CONSTRAINT mfa_challenges_pkey PRIMARY KEY (id);


--
-- Name: mfa_factors mfa_factors_last_challenged_at_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_factors
    ADD CONSTRAINT mfa_factors_last_challenged_at_key UNIQUE (last_challenged_at);


--
-- Name: mfa_factors mfa_factors_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_factors
    ADD CONSTRAINT mfa_factors_pkey PRIMARY KEY (id);


--
-- Name: oauth_authorizations oauth_authorizations_authorization_code_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_authorizations
    ADD CONSTRAINT oauth_authorizations_authorization_code_key UNIQUE (authorization_code);


--
-- Name: oauth_authorizations oauth_authorizations_authorization_id_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_authorizations
    ADD CONSTRAINT oauth_authorizations_authorization_id_key UNIQUE (authorization_id);


--
-- Name: oauth_authorizations oauth_authorizations_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_authorizations
    ADD CONSTRAINT oauth_authorizations_pkey PRIMARY KEY (id);


--
-- Name: oauth_client_states oauth_client_states_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_client_states
    ADD CONSTRAINT oauth_client_states_pkey PRIMARY KEY (id);


--
-- Name: oauth_clients oauth_clients_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_clients
    ADD CONSTRAINT oauth_clients_pkey PRIMARY KEY (id);


--
-- Name: oauth_consents oauth_consents_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_consents
    ADD CONSTRAINT oauth_consents_pkey PRIMARY KEY (id);


--
-- Name: oauth_consents oauth_consents_user_client_unique; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_consents
    ADD CONSTRAINT oauth_consents_user_client_unique UNIQUE (user_id, client_id);


--
-- Name: one_time_tokens one_time_tokens_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.one_time_tokens
    ADD CONSTRAINT one_time_tokens_pkey PRIMARY KEY (id);


--
-- Name: refresh_tokens refresh_tokens_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id);


--
-- Name: refresh_tokens refresh_tokens_token_unique; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.refresh_tokens
    ADD CONSTRAINT refresh_tokens_token_unique UNIQUE (token);


--
-- Name: saml_providers saml_providers_entity_id_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.saml_providers
    ADD CONSTRAINT saml_providers_entity_id_key UNIQUE (entity_id);


--
-- Name: saml_providers saml_providers_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.saml_providers
    ADD CONSTRAINT saml_providers_pkey PRIMARY KEY (id);


--
-- Name: saml_relay_states saml_relay_states_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.saml_relay_states
    ADD CONSTRAINT saml_relay_states_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: sso_domains sso_domains_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.sso_domains
    ADD CONSTRAINT sso_domains_pkey PRIMARY KEY (id);


--
-- Name: sso_providers sso_providers_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.sso_providers
    ADD CONSTRAINT sso_providers_pkey PRIMARY KEY (id);


--
-- Name: users users_phone_key; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.users
    ADD CONSTRAINT users_phone_key UNIQUE (phone);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: webauthn_challenges webauthn_challenges_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.webauthn_challenges
    ADD CONSTRAINT webauthn_challenges_pkey PRIMARY KEY (id);


--
-- Name: webauthn_credentials webauthn_credentials_pkey; Type: CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.webauthn_credentials
    ADD CONSTRAINT webauthn_credentials_pkey PRIMARY KEY (id);


--
-- Name: agent_personas agent_personas_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_personas
    ADD CONSTRAINT agent_personas_key_key UNIQUE (key);


--
-- Name: agent_personas agent_personas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_personas
    ADD CONSTRAINT agent_personas_pkey PRIMARY KEY (id);


--
-- Name: article_chunks article_chunks_article_id_chunk_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_chunks
    ADD CONSTRAINT article_chunks_article_id_chunk_index_key UNIQUE (article_id, chunk_index);


--
-- Name: article_chunks article_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_chunks
    ADD CONSTRAINT article_chunks_pkey PRIMARY KEY (id);


--
-- Name: asset_snapshots asset_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_snapshots
    ADD CONSTRAINT asset_snapshots_pkey PRIMARY KEY (id);


--
-- Name: asset_snapshots asset_snapshots_symbol_asset_type_snapshot_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_snapshots
    ADD CONSTRAINT asset_snapshots_symbol_asset_type_snapshot_type_key UNIQUE (symbol, asset_type, snapshot_type);


--
-- Name: book_chapters book_chapters_book_id_chapter_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_chapters
    ADD CONSTRAINT book_chapters_book_id_chapter_number_key UNIQUE (book_id, chapter_number);


--
-- Name: book_chapters book_chapters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_chapters
    ADD CONSTRAINT book_chapters_pkey PRIMARY KEY (id);


--
-- Name: book_chunks book_chunks_book_id_chunk_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_chunks
    ADD CONSTRAINT book_chunks_book_id_chunk_index_key UNIQUE (book_id, chunk_index);


--
-- Name: book_chunks book_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_chunks
    ADD CONSTRAINT book_chunks_pkey PRIMARY KEY (id);


--
-- Name: books books_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_pkey PRIMARY KEY (id);


--
-- Name: chat_messages chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_pkey PRIMARY KEY (id);


--
-- Name: chat_sessions chat_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_pkey PRIMARY KEY (id);


--
-- Name: company_filing_chunks company_filing_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_filing_chunks
    ADD CONSTRAINT company_filing_chunks_pkey PRIMARY KEY (id);


--
-- Name: company_profile_cache company_profile_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_profile_cache
    ADD CONSTRAINT company_profile_cache_pkey PRIMARY KEY (ticker);


--
-- Name: competitor_intel_audit competitor_intel_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitor_intel_audit
    ADD CONSTRAINT competitor_intel_audit_pkey PRIMARY KEY (id);


--
-- Name: competitor_intel_cache competitor_intel_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competitor_intel_cache
    ADD CONSTRAINT competitor_intel_cache_pkey PRIMARY KEY (ticker);


--
-- Name: crypto_coin_id_cache crypto_coin_id_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crypto_coin_id_cache
    ADD CONSTRAINT crypto_coin_id_cache_pkey PRIMARY KEY (symbol);


--
-- Name: crypto_fundamentals_cache crypto_fundamentals_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crypto_fundamentals_cache
    ADD CONSTRAINT crypto_fundamentals_cache_pkey PRIMARY KEY (id);


--
-- Name: crypto_fundamentals_cache crypto_fundamentals_cache_symbol_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crypto_fundamentals_cache
    ADD CONSTRAINT crypto_fundamentals_cache_symbol_key UNIQUE (symbol);


--
-- Name: crypto_snapshots crypto_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crypto_snapshots
    ADD CONSTRAINT crypto_snapshots_pkey PRIMARY KEY (id);


--
-- Name: crypto_snapshots crypto_snapshots_symbol_category_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crypto_snapshots
    ADD CONSTRAINT crypto_snapshots_symbol_category_key UNIQUE (symbol, category);


--
-- Name: daily_briefings daily_briefings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_briefings
    ADD CONSTRAINT daily_briefings_pkey PRIMARY KEY (id);


--
-- Name: etf_detail_cache etf_detail_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etf_detail_cache
    ADD CONSTRAINT etf_detail_cache_pkey PRIMARY KEY (id);


--
-- Name: etf_detail_cache etf_detail_cache_symbol_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etf_detail_cache
    ADD CONSTRAINT etf_detail_cache_symbol_key UNIQUE (symbol);


--
-- Name: etf_snapshot_cache etf_snapshot_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etf_snapshot_cache
    ADD CONSTRAINT etf_snapshot_cache_pkey PRIMARY KEY (id);


--
-- Name: etf_snapshot_cache etf_snapshot_cache_symbol_category_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.etf_snapshot_cache
    ADD CONSTRAINT etf_snapshot_cache_symbol_category_key UNIQUE (symbol, category);


--
-- Name: geopolitical_macro_audit geopolitical_macro_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.geopolitical_macro_audit
    ADD CONSTRAINT geopolitical_macro_audit_pkey PRIMARY KEY (id);


--
-- Name: geopolitical_macro_cache geopolitical_macro_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.geopolitical_macro_cache
    ADD CONSTRAINT geopolitical_macro_cache_pkey PRIMARY KEY (scope);


--
-- Name: health_check_cache health_check_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.health_check_cache
    ADD CONSTRAINT health_check_cache_pkey PRIMARY KEY (id);


--
-- Name: health_check_cache health_check_cache_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.health_check_cache
    ADD CONSTRAINT health_check_cache_ticker_key UNIQUE (ticker);


--
-- Name: hedge_fund_quarters hedge_fund_quarters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hedge_fund_quarters
    ADD CONSTRAINT hedge_fund_quarters_pkey PRIMARY KEY (id);


--
-- Name: hedge_fund_quarters hedge_fund_quarters_ticker_year_quarter_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hedge_fund_quarters
    ADD CONSTRAINT hedge_fund_quarters_ticker_year_quarter_key UNIQUE (ticker, year, quarter);


--
-- Name: holders_cache holders_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.holders_cache
    ADD CONSTRAINT holders_cache_pkey PRIMARY KEY (id);


--
-- Name: holders_cache holders_cache_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.holders_cache
    ADD CONSTRAINT holders_cache_ticker_key UNIQUE (ticker);


--
-- Name: index_detail_cache index_detail_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.index_detail_cache
    ADD CONSTRAINT index_detail_cache_pkey PRIMARY KEY (cache_key);


--
-- Name: index_macro_forecast_cache index_macro_forecast_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.index_macro_forecast_cache
    ADD CONSTRAINT index_macro_forecast_cache_pkey PRIMARY KEY (symbol);


--
-- Name: industry_dossier industry_dossier_industry_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_dossier
    ADD CONSTRAINT industry_dossier_industry_key UNIQUE (industry);


--
-- Name: industry_dossier industry_dossier_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_dossier
    ADD CONSTRAINT industry_dossier_pkey PRIMARY KEY (id);


--
-- Name: industry_moat_benchmarks industry_moat_benchmarks_industry_pillar_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_moat_benchmarks
    ADD CONSTRAINT industry_moat_benchmarks_industry_pillar_name_key UNIQUE (industry, pillar_name);


--
-- Name: industry_moat_benchmarks industry_moat_benchmarks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_moat_benchmarks
    ADD CONSTRAINT industry_moat_benchmarks_pkey PRIMARY KEY (id);


--
-- Name: industry_override_audit industry_override_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.industry_override_audit
    ADD CONSTRAINT industry_override_audit_pkey PRIMARY KEY (id);


--
-- Name: ip_intel_audit ip_intel_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ip_intel_audit
    ADD CONSTRAINT ip_intel_audit_pkey PRIMARY KEY (id);


--
-- Name: ip_intel_cache ip_intel_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ip_intel_cache
    ADD CONSTRAINT ip_intel_cache_pkey PRIMARY KEY (ticker);


--
-- Name: lessons lessons_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lessons
    ADD CONSTRAINT lessons_pkey PRIMARY KEY (id);


--
-- Name: market_deep_dive_cache market_deep_dive_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_deep_dive_cache
    ADD CONSTRAINT market_deep_dive_cache_pkey PRIMARY KEY (symbol, context_hash);


--
-- Name: market_insights market_insights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_insights
    ADD CONSTRAINT market_insights_pkey PRIMARY KEY (id);


--
-- Name: moat_intel_audit moat_intel_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.moat_intel_audit
    ADD CONSTRAINT moat_intel_audit_pkey PRIMARY KEY (id);


--
-- Name: moat_intel_cache moat_intel_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.moat_intel_cache
    ADD CONSTRAINT moat_intel_cache_pkey PRIMARY KEY (ticker);


--
-- Name: money_move_articles money_move_articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.money_move_articles
    ADD CONSTRAINT money_move_articles_pkey PRIMARY KEY (id);


--
-- Name: news_articles news_articles_external_id_source_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_articles
    ADD CONSTRAINT news_articles_external_id_source_name_key UNIQUE (external_id, source_name);


--
-- Name: news_articles news_articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_articles
    ADD CONSTRAINT news_articles_pkey PRIMARY KEY (id);


--
-- Name: portfolio_holdings portfolio_holdings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_holdings
    ADD CONSTRAINT portfolio_holdings_pkey PRIMARY KEY (id);


--
-- Name: portfolio_holdings portfolio_holdings_user_id_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_holdings
    ADD CONSTRAINT portfolio_holdings_user_id_ticker_key UNIQUE (user_id, ticker);


--
-- Name: portfolio_items portfolio_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_items
    ADD CONSTRAINT portfolio_items_pkey PRIMARY KEY (id);


--
-- Name: portfolio_items portfolio_items_portfolio_id_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_items
    ADD CONSTRAINT portfolio_items_portfolio_id_ticker_key UNIQUE (portfolio_id, ticker);


--
-- Name: portfolios portfolios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_pkey PRIMARY KEY (id);


--
-- Name: portfolios portfolios_user_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_user_id_name_key UNIQUE (user_id, name);


--
-- Name: price_catalyst_audit price_catalyst_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_catalyst_audit
    ADD CONSTRAINT price_catalyst_audit_pkey PRIMARY KEY (id);


--
-- Name: price_catalyst_cache price_catalyst_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_catalyst_cache
    ADD CONSTRAINT price_catalyst_cache_pkey PRIMARY KEY (ticker);


--
-- Name: profit_power_cache profit_power_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profit_power_cache
    ADD CONSTRAINT profit_power_cache_pkey PRIMARY KEY (id);


--
-- Name: profit_power_cache profit_power_cache_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profit_power_cache
    ADD CONSTRAINT profit_power_cache_ticker_key UNIQUE (ticker);


--
-- Name: research_reports research_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_reports
    ADD CONSTRAINT research_reports_pkey PRIMARY KEY (id);


--
-- Name: revenue_breakdown_cache revenue_breakdown_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.revenue_breakdown_cache
    ADD CONSTRAINT revenue_breakdown_cache_pkey PRIMARY KEY (id);


--
-- Name: revenue_breakdown_cache revenue_breakdown_cache_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.revenue_breakdown_cache
    ADD CONSTRAINT revenue_breakdown_cache_ticker_key UNIQUE (ticker);


--
-- Name: sector_aggregates sector_aggregates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sector_aggregates
    ADD CONSTRAINT sector_aggregates_pkey PRIMARY KEY (sector);


--
-- Name: sector_benchmarks sector_benchmarks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sector_benchmarks
    ADD CONSTRAINT sector_benchmarks_pkey PRIMARY KEY (id);


--
-- Name: short_interest_cache short_interest_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.short_interest_cache
    ADD CONSTRAINT short_interest_cache_pkey PRIMARY KEY (id);


--
-- Name: short_interest_cache short_interest_cache_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.short_interest_cache
    ADD CONSTRAINT short_interest_cache_ticker_key UNIQUE (ticker);


--
-- Name: signal_of_confidence_cache signal_of_confidence_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_of_confidence_cache
    ADD CONSTRAINT signal_of_confidence_cache_pkey PRIMARY KEY (id);


--
-- Name: signal_of_confidence_cache signal_of_confidence_cache_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_of_confidence_cache
    ADD CONSTRAINT signal_of_confidence_cache_ticker_key UNIQUE (ticker);


--
-- Name: signals_cache signals_cache_cache_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signals_cache
    ADD CONSTRAINT signals_cache_cache_key_key UNIQUE (cache_key);


--
-- Name: signals_cache signals_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signals_cache
    ADD CONSTRAINT signals_cache_pkey PRIMARY KEY (id);


--
-- Name: snapshot_cache snapshot_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snapshot_cache
    ADD CONSTRAINT snapshot_cache_pkey PRIMARY KEY (id);


--
-- Name: snapshot_cache snapshot_cache_ticker_category_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snapshot_cache
    ADD CONSTRAINT snapshot_cache_ticker_category_key UNIQUE (ticker, category);


--
-- Name: social_mentions_history social_mentions_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_mentions_history
    ADD CONSTRAINT social_mentions_history_pkey PRIMARY KEY (id);


--
-- Name: social_mentions_history social_mentions_history_ticker_snapshot_date_source_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_mentions_history
    ADD CONSTRAINT social_mentions_history_ticker_snapshot_date_source_key UNIQUE (ticker, snapshot_date, source);


--
-- Name: stock_fundamentals_cache stock_fundamentals_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_fundamentals_cache
    ADD CONSTRAINT stock_fundamentals_cache_pkey PRIMARY KEY (id);


--
-- Name: stock_fundamentals_cache stock_fundamentals_cache_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_fundamentals_cache
    ADD CONSTRAINT stock_fundamentals_cache_ticker_key UNIQUE (ticker);


--
-- Name: ticker_data_cache ticker_data_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_data_cache
    ADD CONSTRAINT ticker_data_cache_pkey PRIMARY KEY (ticker);


--
-- Name: ticker_news_cache ticker_news_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_news_cache
    ADD CONSTRAINT ticker_news_cache_pkey PRIMARY KEY (id);


--
-- Name: ticker_news_cache ticker_news_cache_ticker_external_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_news_cache
    ADD CONSTRAINT ticker_news_cache_ticker_external_id_key UNIQUE (ticker, external_id);


--
-- Name: ticker_report_cache ticker_report_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_report_cache
    ADD CONSTRAINT ticker_report_cache_pkey PRIMARY KEY (ticker, persona);


--
-- Name: sector_benchmarks uq_sector_industry_metric_period; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sector_benchmarks
    ADD CONSTRAINT uq_sector_industry_metric_period UNIQUE (sector, industry, metric_name, period_type, period_label);


--
-- Name: user_book_progress user_book_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_book_progress
    ADD CONSTRAINT user_book_progress_pkey PRIMARY KEY (id);


--
-- Name: user_book_progress user_book_progress_user_id_curriculum_order_core_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_book_progress
    ADD CONSTRAINT user_book_progress_user_id_curriculum_order_core_number_key UNIQUE (user_id, curriculum_order, core_number);


--
-- Name: user_bookmarks user_bookmarks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_bookmarks
    ADD CONSTRAINT user_bookmarks_pkey PRIMARY KEY (id);


--
-- Name: user_bookmarks user_bookmarks_user_id_bookmarkable_type_bookmarkable_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_bookmarks
    ADD CONSTRAINT user_bookmarks_user_id_bookmarkable_type_bookmarkable_id_key UNIQUE (user_id, bookmarkable_type, bookmarkable_id);


--
-- Name: user_credits user_credits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_credits
    ADD CONSTRAINT user_credits_pkey PRIMARY KEY (id);


--
-- Name: user_credits user_credits_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_credits
    ADD CONSTRAINT user_credits_user_id_key UNIQUE (user_id);


--
-- Name: user_learn_progress user_learn_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_learn_progress
    ADD CONSTRAINT user_learn_progress_pkey PRIMARY KEY (id);


--
-- Name: user_learn_progress user_learn_progress_user_id_content_type_item_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_learn_progress
    ADD CONSTRAINT user_learn_progress_user_id_content_type_item_key_key UNIQUE (user_id, content_type, item_key);


--
-- Name: user_lesson_progress user_lesson_progress_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_lesson_progress
    ADD CONSTRAINT user_lesson_progress_pkey PRIMARY KEY (id);


--
-- Name: user_lesson_progress user_lesson_progress_user_id_lesson_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_lesson_progress
    ADD CONSTRAINT user_lesson_progress_user_id_lesson_id_key UNIQUE (user_id, lesson_id);


--
-- Name: user_study_schedules user_study_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_study_schedules
    ADD CONSTRAINT user_study_schedules_pkey PRIMARY KEY (id);


--
-- Name: user_study_schedules user_study_schedules_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_study_schedules
    ADD CONSTRAINT user_study_schedules_user_id_key UNIQUE (user_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: watchlist_items watchlist_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_pkey PRIMARY KEY (id);


--
-- Name: watchlist_items watchlist_items_user_id_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_user_id_ticker_key UNIQUE (user_id, ticker);


--
-- Name: whale_alerts whale_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_alerts
    ADD CONSTRAINT whale_alerts_pkey PRIMARY KEY (id);


--
-- Name: whale_filing_snapshots whale_filing_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_filing_snapshots
    ADD CONSTRAINT whale_filing_snapshots_pkey PRIMARY KEY (id);


--
-- Name: whale_filing_snapshots whale_filing_snapshots_whale_id_filing_period_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_filing_snapshots
    ADD CONSTRAINT whale_filing_snapshots_whale_id_filing_period_key UNIQUE (whale_id, filing_period);


--
-- Name: whale_follows whale_follows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_follows
    ADD CONSTRAINT whale_follows_pkey PRIMARY KEY (id);


--
-- Name: whale_follows whale_follows_user_id_whale_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_follows
    ADD CONSTRAINT whale_follows_user_id_whale_id_key UNIQUE (user_id, whale_id);


--
-- Name: whale_holdings whale_holdings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_holdings
    ADD CONSTRAINT whale_holdings_pkey PRIMARY KEY (id);


--
-- Name: whale_holdings whale_holdings_whale_id_ticker_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_holdings
    ADD CONSTRAINT whale_holdings_whale_id_ticker_key UNIQUE (whale_id, ticker);


--
-- Name: whale_profile_cache whale_profile_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_profile_cache
    ADD CONSTRAINT whale_profile_cache_pkey PRIMARY KEY (whale_id);


--
-- Name: whale_sector_allocations whale_sector_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_sector_allocations
    ADD CONSTRAINT whale_sector_allocations_pkey PRIMARY KEY (id);


--
-- Name: whale_sector_allocations whale_sector_allocations_whale_id_sector_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_sector_allocations
    ADD CONSTRAINT whale_sector_allocations_whale_id_sector_key UNIQUE (whale_id, sector);


--
-- Name: whale_trade_groups whale_trade_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_trade_groups
    ADD CONSTRAINT whale_trade_groups_pkey PRIMARY KEY (id);


--
-- Name: whale_trades whale_trades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_trades
    ADD CONSTRAINT whale_trades_pkey PRIMARY KEY (id);


--
-- Name: whales whales_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whales
    ADD CONSTRAINT whales_pkey PRIMARY KEY (id);


--
-- Name: messages messages_payload_exclusive; Type: CHECK CONSTRAINT; Schema: realtime; Owner: -
--

ALTER TABLE realtime.messages
    ADD CONSTRAINT messages_payload_exclusive CHECK (((payload IS NULL) OR (binary_payload IS NULL))) NOT VALID;


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: realtime; Owner: -
--

ALTER TABLE ONLY realtime.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id, inserted_at);


--
-- Name: subscription pk_subscription; Type: CONSTRAINT; Schema: realtime; Owner: -
--

ALTER TABLE ONLY realtime.subscription
    ADD CONSTRAINT pk_subscription PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: realtime; Owner: -
--

ALTER TABLE ONLY realtime.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: buckets_analytics buckets_analytics_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.buckets_analytics
    ADD CONSTRAINT buckets_analytics_pkey PRIMARY KEY (id);


--
-- Name: buckets buckets_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.buckets
    ADD CONSTRAINT buckets_pkey PRIMARY KEY (id);


--
-- Name: buckets_vectors buckets_vectors_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.buckets_vectors
    ADD CONSTRAINT buckets_vectors_pkey PRIMARY KEY (id);


--
-- Name: migrations migrations_name_key; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.migrations
    ADD CONSTRAINT migrations_name_key UNIQUE (name);


--
-- Name: migrations migrations_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.migrations
    ADD CONSTRAINT migrations_pkey PRIMARY KEY (id);


--
-- Name: objects objects_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.objects
    ADD CONSTRAINT objects_pkey PRIMARY KEY (id);


--
-- Name: s3_multipart_uploads_parts s3_multipart_uploads_parts_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.s3_multipart_uploads_parts
    ADD CONSTRAINT s3_multipart_uploads_parts_pkey PRIMARY KEY (id);


--
-- Name: s3_multipart_uploads s3_multipart_uploads_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.s3_multipart_uploads
    ADD CONSTRAINT s3_multipart_uploads_pkey PRIMARY KEY (id);


--
-- Name: vector_indexes vector_indexes_pkey; Type: CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.vector_indexes
    ADD CONSTRAINT vector_indexes_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: supabase_migrations; Owner: -
--

ALTER TABLE ONLY supabase_migrations.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: audit_logs_instance_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX audit_logs_instance_id_idx ON auth.audit_log_entries USING btree (instance_id);


--
-- Name: confirmation_token_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX confirmation_token_idx ON auth.users USING btree (confirmation_token) WHERE ((confirmation_token)::text !~ '^[0-9 ]*$'::text);


--
-- Name: custom_oauth_providers_created_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX custom_oauth_providers_created_at_idx ON auth.custom_oauth_providers USING btree (created_at);


--
-- Name: custom_oauth_providers_enabled_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX custom_oauth_providers_enabled_idx ON auth.custom_oauth_providers USING btree (enabled);


--
-- Name: custom_oauth_providers_identifier_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX custom_oauth_providers_identifier_idx ON auth.custom_oauth_providers USING btree (identifier);


--
-- Name: custom_oauth_providers_provider_type_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX custom_oauth_providers_provider_type_idx ON auth.custom_oauth_providers USING btree (provider_type);


--
-- Name: email_change_token_current_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX email_change_token_current_idx ON auth.users USING btree (email_change_token_current) WHERE ((email_change_token_current)::text !~ '^[0-9 ]*$'::text);


--
-- Name: email_change_token_new_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX email_change_token_new_idx ON auth.users USING btree (email_change_token_new) WHERE ((email_change_token_new)::text !~ '^[0-9 ]*$'::text);


--
-- Name: factor_id_created_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX factor_id_created_at_idx ON auth.mfa_factors USING btree (user_id, created_at);


--
-- Name: flow_state_created_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX flow_state_created_at_idx ON auth.flow_state USING btree (created_at DESC);


--
-- Name: identities_email_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX identities_email_idx ON auth.identities USING btree (email text_pattern_ops);


--
-- Name: INDEX identities_email_idx; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON INDEX auth.identities_email_idx IS 'Auth: Ensures indexed queries on the email column';


--
-- Name: identities_user_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX identities_user_id_idx ON auth.identities USING btree (user_id);


--
-- Name: idx_auth_code; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX idx_auth_code ON auth.flow_state USING btree (auth_code);


--
-- Name: idx_oauth_client_states_created_at; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX idx_oauth_client_states_created_at ON auth.oauth_client_states USING btree (created_at);


--
-- Name: idx_user_id_auth_method; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX idx_user_id_auth_method ON auth.flow_state USING btree (user_id, authentication_method);


--
-- Name: mfa_challenge_created_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX mfa_challenge_created_at_idx ON auth.mfa_challenges USING btree (created_at DESC);


--
-- Name: mfa_factors_user_friendly_name_unique; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX mfa_factors_user_friendly_name_unique ON auth.mfa_factors USING btree (friendly_name, user_id) WHERE (TRIM(BOTH FROM friendly_name) <> ''::text);


--
-- Name: mfa_factors_user_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX mfa_factors_user_id_idx ON auth.mfa_factors USING btree (user_id);


--
-- Name: oauth_auth_pending_exp_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX oauth_auth_pending_exp_idx ON auth.oauth_authorizations USING btree (expires_at) WHERE (status = 'pending'::auth.oauth_authorization_status);


--
-- Name: oauth_clients_deleted_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX oauth_clients_deleted_at_idx ON auth.oauth_clients USING btree (deleted_at);


--
-- Name: oauth_consents_active_client_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX oauth_consents_active_client_idx ON auth.oauth_consents USING btree (client_id) WHERE (revoked_at IS NULL);


--
-- Name: oauth_consents_active_user_client_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX oauth_consents_active_user_client_idx ON auth.oauth_consents USING btree (user_id, client_id) WHERE (revoked_at IS NULL);


--
-- Name: oauth_consents_user_order_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX oauth_consents_user_order_idx ON auth.oauth_consents USING btree (user_id, granted_at DESC);


--
-- Name: one_time_tokens_relates_to_hash_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX one_time_tokens_relates_to_hash_idx ON auth.one_time_tokens USING hash (relates_to);


--
-- Name: one_time_tokens_token_hash_hash_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX one_time_tokens_token_hash_hash_idx ON auth.one_time_tokens USING hash (token_hash);


--
-- Name: one_time_tokens_user_id_token_type_key; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX one_time_tokens_user_id_token_type_key ON auth.one_time_tokens USING btree (user_id, token_type);


--
-- Name: reauthentication_token_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX reauthentication_token_idx ON auth.users USING btree (reauthentication_token) WHERE ((reauthentication_token)::text !~ '^[0-9 ]*$'::text);


--
-- Name: recovery_token_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX recovery_token_idx ON auth.users USING btree (recovery_token) WHERE ((recovery_token)::text !~ '^[0-9 ]*$'::text);


--
-- Name: refresh_tokens_instance_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX refresh_tokens_instance_id_idx ON auth.refresh_tokens USING btree (instance_id);


--
-- Name: refresh_tokens_instance_id_user_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX refresh_tokens_instance_id_user_id_idx ON auth.refresh_tokens USING btree (instance_id, user_id);


--
-- Name: refresh_tokens_parent_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX refresh_tokens_parent_idx ON auth.refresh_tokens USING btree (parent);


--
-- Name: refresh_tokens_session_id_revoked_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX refresh_tokens_session_id_revoked_idx ON auth.refresh_tokens USING btree (session_id, revoked);


--
-- Name: refresh_tokens_updated_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX refresh_tokens_updated_at_idx ON auth.refresh_tokens USING btree (updated_at DESC);


--
-- Name: saml_providers_sso_provider_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX saml_providers_sso_provider_id_idx ON auth.saml_providers USING btree (sso_provider_id);


--
-- Name: saml_relay_states_created_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX saml_relay_states_created_at_idx ON auth.saml_relay_states USING btree (created_at DESC);


--
-- Name: saml_relay_states_for_email_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX saml_relay_states_for_email_idx ON auth.saml_relay_states USING btree (for_email);


--
-- Name: saml_relay_states_sso_provider_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX saml_relay_states_sso_provider_id_idx ON auth.saml_relay_states USING btree (sso_provider_id);


--
-- Name: sessions_not_after_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX sessions_not_after_idx ON auth.sessions USING btree (not_after DESC);


--
-- Name: sessions_oauth_client_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX sessions_oauth_client_id_idx ON auth.sessions USING btree (oauth_client_id);


--
-- Name: sessions_user_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX sessions_user_id_idx ON auth.sessions USING btree (user_id);


--
-- Name: sso_domains_domain_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX sso_domains_domain_idx ON auth.sso_domains USING btree (lower(domain));


--
-- Name: sso_domains_sso_provider_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX sso_domains_sso_provider_id_idx ON auth.sso_domains USING btree (sso_provider_id);


--
-- Name: sso_providers_resource_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX sso_providers_resource_id_idx ON auth.sso_providers USING btree (lower(resource_id));


--
-- Name: sso_providers_resource_id_pattern_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX sso_providers_resource_id_pattern_idx ON auth.sso_providers USING btree (resource_id text_pattern_ops);


--
-- Name: unique_phone_factor_per_user; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX unique_phone_factor_per_user ON auth.mfa_factors USING btree (user_id, phone);


--
-- Name: user_id_created_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX user_id_created_at_idx ON auth.sessions USING btree (user_id, created_at);


--
-- Name: users_email_partial_key; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX users_email_partial_key ON auth.users USING btree (email) WHERE (is_sso_user = false);


--
-- Name: INDEX users_email_partial_key; Type: COMMENT; Schema: auth; Owner: -
--

COMMENT ON INDEX auth.users_email_partial_key IS 'Auth: A partial unique index that applies only when is_sso_user is false';


--
-- Name: users_instance_id_email_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX users_instance_id_email_idx ON auth.users USING btree (instance_id, lower((email)::text));


--
-- Name: users_instance_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX users_instance_id_idx ON auth.users USING btree (instance_id);


--
-- Name: users_is_anonymous_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX users_is_anonymous_idx ON auth.users USING btree (is_anonymous);


--
-- Name: webauthn_challenges_expires_at_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX webauthn_challenges_expires_at_idx ON auth.webauthn_challenges USING btree (expires_at);


--
-- Name: webauthn_challenges_user_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX webauthn_challenges_user_id_idx ON auth.webauthn_challenges USING btree (user_id);


--
-- Name: webauthn_credentials_credential_id_key; Type: INDEX; Schema: auth; Owner: -
--

CREATE UNIQUE INDEX webauthn_credentials_credential_id_key ON auth.webauthn_credentials USING btree (credential_id);


--
-- Name: webauthn_credentials_user_id_idx; Type: INDEX; Schema: auth; Owner: -
--

CREATE INDEX webauthn_credentials_user_id_idx ON auth.webauthn_credentials USING btree (user_id);


--
-- Name: idx_agent_personas_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_personas_active ON public.agent_personas USING btree (is_active) WHERE (is_active = true);


--
-- Name: idx_agent_personas_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_personas_key ON public.agent_personas USING btree (key);


--
-- Name: idx_article_chunks_article; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_chunks_article ON public.article_chunks USING btree (article_id);


--
-- Name: idx_article_chunks_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_chunks_embedding_hnsw ON public.article_chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');


--
-- Name: idx_book_chapters_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_chapters_book ON public.book_chapters USING btree (book_id, chapter_number);


--
-- Name: idx_book_chunks_book; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_chunks_book ON public.book_chunks USING btree (book_id);


--
-- Name: idx_book_chunks_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_book_chunks_embedding_hnsw ON public.book_chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');


--
-- Name: idx_bookmarks_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bookmarks_target ON public.user_bookmarks USING btree (bookmarkable_type, bookmarkable_id);


--
-- Name: idx_bookmarks_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bookmarks_user ON public.user_bookmarks USING btree (user_id);


--
-- Name: idx_bookmarks_user_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bookmarks_user_type ON public.user_bookmarks USING btree (user_id, bookmarkable_type);


--
-- Name: idx_books_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_level ON public.books USING btree (level);


--
-- Name: idx_books_rating; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_books_rating ON public.books USING btree (rating DESC);


--
-- Name: idx_chat_messages_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_messages_session ON public.chat_messages USING btree (session_id, created_at);


--
-- Name: idx_chat_sessions_saved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_saved ON public.chat_sessions USING btree (user_id, is_saved) WHERE (is_saved = true);


--
-- Name: idx_chat_sessions_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_type ON public.chat_sessions USING btree (session_type);


--
-- Name: idx_chat_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_user ON public.chat_sessions USING btree (user_id, last_message_at DESC);


--
-- Name: idx_competitor_intel_audit_computed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_competitor_intel_audit_computed_at ON public.competitor_intel_audit USING btree (computed_at DESC);


--
-- Name: idx_competitor_intel_audit_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_competitor_intel_audit_run_id ON public.competitor_intel_audit USING btree (run_id);


--
-- Name: idx_competitor_intel_audit_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_competitor_intel_audit_ticker ON public.competitor_intel_audit USING btree (ticker, computed_at DESC);


--
-- Name: idx_competitor_intel_cache_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_competitor_intel_cache_expires ON public.competitor_intel_cache USING btree (expires_at);


--
-- Name: idx_crypto_fundamentals_cache_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crypto_fundamentals_cache_symbol ON public.crypto_fundamentals_cache USING btree (symbol);


--
-- Name: idx_crypto_snapshots_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crypto_snapshots_symbol ON public.crypto_snapshots USING btree (symbol);


--
-- Name: idx_daily_briefings_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_daily_briefings_active ON public.daily_briefings USING btree (is_active, priority DESC) WHERE (is_active = true);


--
-- Name: idx_etf_detail_cache_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_etf_detail_cache_symbol ON public.etf_detail_cache USING btree (symbol);


--
-- Name: idx_etf_snapshot_cache_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_etf_snapshot_cache_symbol ON public.etf_snapshot_cache USING btree (symbol);


--
-- Name: idx_filing_chunks_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filing_chunks_embedding_hnsw ON public.company_filing_chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');


--
-- Name: idx_filing_chunks_filing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filing_chunks_filing ON public.company_filing_chunks USING btree (ticker, filing_type, fiscal_year);


--
-- Name: idx_filing_chunks_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filing_chunks_ticker ON public.company_filing_chunks USING btree (ticker);


--
-- Name: idx_filing_chunks_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_filing_chunks_unique ON public.company_filing_chunks USING btree (ticker, filing_type, fiscal_year, COALESCE(fiscal_quarter, 0), chunk_index);


--
-- Name: idx_filing_snapshots_whale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filing_snapshots_whale ON public.whale_filing_snapshots USING btree (whale_id, processed_at DESC);


--
-- Name: idx_geopolitical_macro_audit_computed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_geopolitical_macro_audit_computed ON public.geopolitical_macro_audit USING btree (computed_at DESC);


--
-- Name: idx_geopolitical_macro_audit_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_geopolitical_macro_audit_run_id ON public.geopolitical_macro_audit USING btree (run_id);


--
-- Name: idx_geopolitical_macro_cache_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_geopolitical_macro_cache_expires ON public.geopolitical_macro_cache USING btree (expires_at);


--
-- Name: idx_health_check_cache_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_health_check_cache_ticker ON public.health_check_cache USING btree (ticker);


--
-- Name: idx_hfq_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hfq_ticker ON public.hedge_fund_quarters USING btree (ticker);


--
-- Name: idx_holders_cache_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_holders_cache_ticker ON public.holders_cache USING btree (ticker);


--
-- Name: idx_index_detail_cache_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_index_detail_cache_symbol ON public.index_detail_cache USING btree (symbol);


--
-- Name: idx_industry_dossier_computed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_industry_dossier_computed_at ON public.industry_dossier USING btree (computed_at DESC);


--
-- Name: idx_industry_dossier_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_industry_dossier_lookup ON public.industry_dossier USING btree (industry, expires_at);


--
-- Name: idx_industry_dossier_sector; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_industry_dossier_sector ON public.industry_dossier USING btree (sector);


--
-- Name: idx_industry_moat_benchmarks_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_industry_moat_benchmarks_lookup ON public.industry_moat_benchmarks USING btree (industry);


--
-- Name: idx_industry_override_audit_computed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_industry_override_audit_computed_at ON public.industry_override_audit USING btree (computed_at DESC);


--
-- Name: idx_industry_override_audit_industry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_industry_override_audit_industry ON public.industry_override_audit USING btree (industry, computed_at DESC);


--
-- Name: idx_industry_override_audit_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_industry_override_audit_run_id ON public.industry_override_audit USING btree (run_id);


--
-- Name: idx_ip_intel_audit_computed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ip_intel_audit_computed_at ON public.ip_intel_audit USING btree (computed_at DESC);


--
-- Name: idx_ip_intel_audit_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ip_intel_audit_run_id ON public.ip_intel_audit USING btree (run_id);


--
-- Name: idx_ip_intel_audit_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ip_intel_audit_ticker ON public.ip_intel_audit USING btree (ticker, computed_at DESC);


--
-- Name: idx_ip_intel_cache_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ip_intel_cache_expires ON public.ip_intel_cache USING btree (expires_at);


--
-- Name: idx_lesson_progress_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lesson_progress_status ON public.user_lesson_progress USING btree (user_id, status);


--
-- Name: idx_lesson_progress_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lesson_progress_user ON public.user_lesson_progress USING btree (user_id);


--
-- Name: idx_lessons_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lessons_category ON public.lessons USING btree (category);


--
-- Name: idx_lessons_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lessons_level ON public.lessons USING btree (level, sort_order);


--
-- Name: idx_market_insights_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_market_insights_created ON public.market_insights USING btree (created_at DESC);


--
-- Name: idx_moat_intel_audit_computed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_moat_intel_audit_computed_at ON public.moat_intel_audit USING btree (computed_at DESC);


--
-- Name: idx_moat_intel_audit_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_moat_intel_audit_run_id ON public.moat_intel_audit USING btree (run_id);


--
-- Name: idx_moat_intel_audit_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_moat_intel_audit_ticker ON public.moat_intel_audit USING btree (ticker, computed_at DESC);


--
-- Name: idx_moat_intel_cache_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_moat_intel_cache_expires ON public.moat_intel_cache USING btree (expires_at);


--
-- Name: idx_money_move_articles_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_money_move_articles_slug ON public.money_move_articles USING btree (slug);


--
-- Name: idx_money_move_articles_sort; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_money_move_articles_sort ON public.money_move_articles USING btree (sort_order);


--
-- Name: idx_money_moves_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_money_moves_category ON public.money_move_articles USING btree (category);


--
-- Name: idx_news_breaking; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_breaking ON public.news_articles USING btree (is_breaking, published_at DESC) WHERE (is_breaking = true);


--
-- Name: idx_news_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_category ON public.news_articles USING btree (category) WHERE (category IS NOT NULL);


--
-- Name: idx_news_published; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_published ON public.news_articles USING btree (published_at DESC);


--
-- Name: idx_news_related_tickers; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_related_tickers ON public.news_articles USING gin (related_tickers jsonb_path_ops);


--
-- Name: idx_news_sentiment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_sentiment ON public.news_articles USING btree (sentiment) WHERE (sentiment IS NOT NULL);


--
-- Name: idx_news_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_news_source ON public.news_articles USING btree (source_name);


--
-- Name: idx_portfolio_holdings_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portfolio_holdings_user ON public.portfolio_holdings USING btree (user_id);


--
-- Name: idx_portfolio_holdings_user_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portfolio_holdings_user_ticker ON public.portfolio_holdings USING btree (user_id, ticker);


--
-- Name: idx_portfolio_items_portfolio; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portfolio_items_portfolio ON public.portfolio_items USING btree (portfolio_id, "position");


--
-- Name: idx_portfolios_user_sort; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portfolios_user_sort ON public.portfolios USING btree (user_id, sort_order);


--
-- Name: idx_price_catalyst_audit_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_catalyst_audit_run_id ON public.price_catalyst_audit USING btree (run_id);


--
-- Name: idx_price_catalyst_audit_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_catalyst_audit_ticker ON public.price_catalyst_audit USING btree (ticker, computed_at DESC);


--
-- Name: idx_price_catalyst_cache_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_catalyst_cache_expires ON public.price_catalyst_cache USING btree (expires_at);


--
-- Name: idx_profit_power_cache_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_profit_power_cache_ticker ON public.profit_power_cache USING btree (ticker);


--
-- Name: idx_reports_persona; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_persona ON public.research_reports USING btree (investor_persona);


--
-- Name: idx_reports_status_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_status_pending ON public.research_reports USING btree (status, created_at) WHERE (status = ANY (ARRAY['pending'::public.report_status, 'processing'::public.report_status]));


--
-- Name: idx_reports_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_ticker ON public.research_reports USING btree (ticker);


--
-- Name: idx_reports_ticker_persona_completed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_ticker_persona_completed ON public.research_reports USING btree (ticker, investor_persona, completed_at DESC) WHERE (status = 'completed'::public.report_status);


--
-- Name: idx_reports_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_user ON public.research_reports USING btree (user_id, created_at DESC);


--
-- Name: idx_reports_user_completed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_user_completed ON public.research_reports USING btree (user_id, created_at DESC) WHERE (status = 'completed'::public.report_status);


--
-- Name: idx_reports_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_user_status ON public.research_reports USING btree (user_id, status);


--
-- Name: idx_research_reports_cache; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_research_reports_cache ON public.research_reports USING btree (ticker, investor_persona, completed_at DESC) WHERE ((status = 'completed'::public.report_status) AND (ticker_report_data IS NOT NULL));


--
-- Name: INDEX idx_research_reports_cache; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_research_reports_cache IS 'Partial index for ticker report cache lookups. Covers completed reports with stored TickerReportResponse data.';


--
-- Name: idx_revenue_breakdown_cache_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_revenue_breakdown_cache_ticker ON public.revenue_breakdown_cache USING btree (ticker);


--
-- Name: idx_sector_benchmarks_computed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sector_benchmarks_computed_at ON public.sector_benchmarks USING btree (computed_at);


--
-- Name: idx_sector_benchmarks_industry_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sector_benchmarks_industry_lookup ON public.sector_benchmarks USING btree (industry, metric_name, period_type);


--
-- Name: idx_sector_benchmarks_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sector_benchmarks_lookup ON public.sector_benchmarks USING btree (sector, metric_name, period_type);


--
-- Name: idx_short_interest_cache_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_short_interest_cache_ticker ON public.short_interest_cache USING btree (ticker);


--
-- Name: idx_signal_of_confidence_cache_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signal_of_confidence_cache_ticker ON public.signal_of_confidence_cache USING btree (ticker);


--
-- Name: idx_signals_cache_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signals_cache_lookup ON public.signals_cache USING btree (cache_key, expires_at);


--
-- Name: idx_snapshots_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshots_expires ON public.asset_snapshots USING btree (expires_at) WHERE (expires_at IS NOT NULL);


--
-- Name: idx_snapshots_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshots_symbol ON public.asset_snapshots USING btree (symbol, asset_type);


--
-- Name: idx_snapshots_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshots_type ON public.asset_snapshots USING btree (asset_type, snapshot_type);


--
-- Name: idx_social_mentions_ticker_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_mentions_ticker_date ON public.social_mentions_history USING btree (ticker, snapshot_date DESC);


--
-- Name: idx_stock_fundamentals_cache_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stock_fundamentals_cache_ticker ON public.stock_fundamentals_cache USING btree (ticker);


--
-- Name: idx_study_schedules_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_study_schedules_user ON public.user_study_schedules USING btree (user_id);


--
-- Name: idx_ticker_data_cache_cached_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ticker_data_cache_cached_at ON public.ticker_data_cache USING btree (cached_at DESC);


--
-- Name: idx_ticker_news_cache_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ticker_news_cache_expires ON public.ticker_news_cache USING btree (expires_at);


--
-- Name: idx_ticker_news_cache_ticker_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ticker_news_cache_ticker_expires ON public.ticker_news_cache USING btree (ticker, expires_at DESC);


--
-- Name: idx_ticker_report_cache_cached_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ticker_report_cache_cached_at ON public.ticker_report_cache USING btree (cached_at DESC);


--
-- Name: idx_user_book_progress_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_book_progress_user ON public.user_book_progress USING btree (user_id);


--
-- Name: idx_user_credits_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_credits_user ON public.user_credits USING btree (user_id);


--
-- Name: idx_user_learn_progress_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_learn_progress_lookup ON public.user_learn_progress USING btree (user_id, content_type);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_users_tier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_tier ON public.users USING btree (tier);


--
-- Name: idx_watchlist_items_needs_enrich; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlist_items_needs_enrich ON public.watchlist_items USING btree (user_id) WHERE (sector IS NULL);


--
-- Name: idx_watchlist_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlist_user ON public.watchlist_items USING btree (user_id);


--
-- Name: idx_watchlist_user_added; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlist_user_added ON public.watchlist_items USING btree (user_id, added_at DESC);


--
-- Name: idx_whale_alerts_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_alerts_active ON public.whale_alerts USING btree (is_active, created_at DESC) WHERE (is_active = true);


--
-- Name: idx_whale_alerts_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_alerts_expires_at ON public.whale_alerts USING btree (expires_at) WHERE (expires_at IS NOT NULL);


--
-- Name: idx_whale_alerts_whale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_alerts_whale ON public.whale_alerts USING btree (whale_id) WHERE (whale_id IS NOT NULL);


--
-- Name: idx_whale_follows_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_follows_user ON public.whale_follows USING btree (user_id);


--
-- Name: idx_whale_follows_whale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_follows_whale ON public.whale_follows USING btree (whale_id);


--
-- Name: idx_whale_holdings_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_holdings_ticker ON public.whale_holdings USING btree (ticker);


--
-- Name: idx_whale_holdings_whale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_holdings_whale ON public.whale_holdings USING btree (whale_id);


--
-- Name: idx_whale_profile_cache_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_profile_cache_ts ON public.whale_profile_cache USING btree (cached_at DESC);


--
-- Name: idx_whale_sectors_whale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_sectors_whale ON public.whale_sector_allocations USING btree (whale_id);


--
-- Name: idx_whale_trade_groups_whale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_trade_groups_whale ON public.whale_trade_groups USING btree (whale_id, created_at DESC);


--
-- Name: idx_whale_trades_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_trades_group ON public.whale_trades USING btree (trade_group_id);


--
-- Name: idx_whale_trades_ticker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_trades_ticker ON public.whale_trades USING btree (ticker);


--
-- Name: idx_whale_trades_whale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whale_trades_whale ON public.whale_trades USING btree (whale_id, created_at DESC);


--
-- Name: idx_whales_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whales_category ON public.whales USING btree (category);


--
-- Name: idx_whales_cik; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whales_cik ON public.whales USING btree (cik) WHERE (cik IS NOT NULL);


--
-- Name: idx_whales_last_hydrated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whales_last_hydrated_at ON public.whales USING btree (last_hydrated_at NULLS FIRST);


--
-- Name: idx_whales_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whales_name ON public.whales USING btree (name);


--
-- Name: uq_whale_trade_groups_whale_date; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_whale_trade_groups_whale_date ON public.whale_trade_groups USING btree (whale_id, date);


--
-- Name: ix_realtime_subscription_entity; Type: INDEX; Schema: realtime; Owner: -
--

CREATE INDEX ix_realtime_subscription_entity ON realtime.subscription USING btree (entity);


--
-- Name: messages_inserted_at_topic_index; Type: INDEX; Schema: realtime; Owner: -
--

CREATE INDEX messages_inserted_at_topic_index ON ONLY realtime.messages USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));


--
-- Name: subscription_subscription_id_entity_filters_action_filter_selec; Type: INDEX; Schema: realtime; Owner: -
--

CREATE UNIQUE INDEX subscription_subscription_id_entity_filters_action_filter_selec ON realtime.subscription USING btree (subscription_id, entity, filters, action_filter, COALESCE(selected_columns, '{}'::text[]));


--
-- Name: bname; Type: INDEX; Schema: storage; Owner: -
--

CREATE UNIQUE INDEX bname ON storage.buckets USING btree (name);


--
-- Name: bucketid_objname; Type: INDEX; Schema: storage; Owner: -
--

CREATE UNIQUE INDEX bucketid_objname ON storage.objects USING btree (bucket_id, name);


--
-- Name: buckets_analytics_unique_name_idx; Type: INDEX; Schema: storage; Owner: -
--

CREATE UNIQUE INDEX buckets_analytics_unique_name_idx ON storage.buckets_analytics USING btree (name) WHERE (deleted_at IS NULL);


--
-- Name: idx_multipart_uploads_list; Type: INDEX; Schema: storage; Owner: -
--

CREATE INDEX idx_multipart_uploads_list ON storage.s3_multipart_uploads USING btree (bucket_id, key, created_at);


--
-- Name: idx_objects_bucket_id_name; Type: INDEX; Schema: storage; Owner: -
--

CREATE INDEX idx_objects_bucket_id_name ON storage.objects USING btree (bucket_id, name COLLATE "C");


--
-- Name: idx_objects_bucket_id_name_lower; Type: INDEX; Schema: storage; Owner: -
--

CREATE INDEX idx_objects_bucket_id_name_lower ON storage.objects USING btree (bucket_id, lower(name) COLLATE "C");


--
-- Name: name_prefix_search; Type: INDEX; Schema: storage; Owner: -
--

CREATE INDEX name_prefix_search ON storage.objects USING btree (name text_pattern_ops);


--
-- Name: vector_indexes_name_bucket_id_idx; Type: INDEX; Schema: storage; Owner: -
--

CREATE UNIQUE INDEX vector_indexes_name_bucket_id_idx ON storage.vector_indexes USING btree (name, bucket_id);


--
-- Name: users on_auth_user_created; Type: TRIGGER; Schema: auth; Owner: -
--

CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();


--
-- Name: agent_personas trg_agent_personas_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_agent_personas_updated_at BEFORE UPDATE ON public.agent_personas FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: chat_messages trg_chat_message_count; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_chat_message_count AFTER INSERT ON public.chat_messages FOR EACH ROW EXECUTE FUNCTION public.increment_chat_message_count();


--
-- Name: users trg_create_user_credits; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_create_user_credits AFTER INSERT ON public.users FOR EACH ROW EXECUTE FUNCTION public.create_user_credits();


--
-- Name: user_study_schedules trg_study_schedules_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_study_schedules_updated_at BEFORE UPDATE ON public.user_study_schedules FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: user_credits trg_user_credits_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_credits_updated_at BEFORE UPDATE ON public.user_credits FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: users trg_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: whale_follows trg_whale_follow_decrement; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_whale_follow_decrement AFTER DELETE ON public.whale_follows FOR EACH ROW EXECUTE FUNCTION public.update_whale_followers_count();


--
-- Name: whale_follows trg_whale_follow_increment; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_whale_follow_increment AFTER INSERT ON public.whale_follows FOR EACH ROW EXECUTE FUNCTION public.update_whale_followers_count();


--
-- Name: whales trg_whales_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_whales_updated_at BEFORE UPDATE ON public.whales FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: subscription tr_check_filters; Type: TRIGGER; Schema: realtime; Owner: -
--

CREATE TRIGGER tr_check_filters BEFORE INSERT OR UPDATE ON realtime.subscription FOR EACH ROW EXECUTE FUNCTION realtime.subscription_check_filters();


--
-- Name: buckets enforce_bucket_name_length_trigger; Type: TRIGGER; Schema: storage; Owner: -
--

CREATE TRIGGER enforce_bucket_name_length_trigger BEFORE INSERT OR UPDATE OF name ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.enforce_bucket_name_length();


--
-- Name: buckets protect_buckets_delete; Type: TRIGGER; Schema: storage; Owner: -
--

CREATE TRIGGER protect_buckets_delete BEFORE DELETE ON storage.buckets FOR EACH STATEMENT EXECUTE FUNCTION storage.protect_delete();


--
-- Name: objects protect_objects_delete; Type: TRIGGER; Schema: storage; Owner: -
--

CREATE TRIGGER protect_objects_delete BEFORE DELETE ON storage.objects FOR EACH STATEMENT EXECUTE FUNCTION storage.protect_delete();


--
-- Name: objects update_objects_updated_at; Type: TRIGGER; Schema: storage; Owner: -
--

CREATE TRIGGER update_objects_updated_at BEFORE UPDATE ON storage.objects FOR EACH ROW EXECUTE FUNCTION storage.update_updated_at_column();


--
-- Name: identities identities_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.identities
    ADD CONSTRAINT identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: mfa_amr_claims mfa_amr_claims_session_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_amr_claims
    ADD CONSTRAINT mfa_amr_claims_session_id_fkey FOREIGN KEY (session_id) REFERENCES auth.sessions(id) ON DELETE CASCADE;


--
-- Name: mfa_challenges mfa_challenges_auth_factor_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_challenges
    ADD CONSTRAINT mfa_challenges_auth_factor_id_fkey FOREIGN KEY (factor_id) REFERENCES auth.mfa_factors(id) ON DELETE CASCADE;


--
-- Name: mfa_factors mfa_factors_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.mfa_factors
    ADD CONSTRAINT mfa_factors_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: oauth_authorizations oauth_authorizations_client_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_authorizations
    ADD CONSTRAINT oauth_authorizations_client_id_fkey FOREIGN KEY (client_id) REFERENCES auth.oauth_clients(id) ON DELETE CASCADE;


--
-- Name: oauth_authorizations oauth_authorizations_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_authorizations
    ADD CONSTRAINT oauth_authorizations_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: oauth_consents oauth_consents_client_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_consents
    ADD CONSTRAINT oauth_consents_client_id_fkey FOREIGN KEY (client_id) REFERENCES auth.oauth_clients(id) ON DELETE CASCADE;


--
-- Name: oauth_consents oauth_consents_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.oauth_consents
    ADD CONSTRAINT oauth_consents_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: one_time_tokens one_time_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.one_time_tokens
    ADD CONSTRAINT one_time_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: refresh_tokens refresh_tokens_session_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.refresh_tokens
    ADD CONSTRAINT refresh_tokens_session_id_fkey FOREIGN KEY (session_id) REFERENCES auth.sessions(id) ON DELETE CASCADE;


--
-- Name: saml_providers saml_providers_sso_provider_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.saml_providers
    ADD CONSTRAINT saml_providers_sso_provider_id_fkey FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE;


--
-- Name: saml_relay_states saml_relay_states_flow_state_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.saml_relay_states
    ADD CONSTRAINT saml_relay_states_flow_state_id_fkey FOREIGN KEY (flow_state_id) REFERENCES auth.flow_state(id) ON DELETE CASCADE;


--
-- Name: saml_relay_states saml_relay_states_sso_provider_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.saml_relay_states
    ADD CONSTRAINT saml_relay_states_sso_provider_id_fkey FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE;


--
-- Name: sessions sessions_oauth_client_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.sessions
    ADD CONSTRAINT sessions_oauth_client_id_fkey FOREIGN KEY (oauth_client_id) REFERENCES auth.oauth_clients(id) ON DELETE CASCADE;


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: sso_domains sso_domains_sso_provider_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.sso_domains
    ADD CONSTRAINT sso_domains_sso_provider_id_fkey FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id) ON DELETE CASCADE;


--
-- Name: webauthn_challenges webauthn_challenges_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.webauthn_challenges
    ADD CONSTRAINT webauthn_challenges_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: webauthn_credentials webauthn_credentials_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: -
--

ALTER TABLE ONLY auth.webauthn_credentials
    ADD CONSTRAINT webauthn_credentials_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: book_chapters book_chapters_book_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_chapters
    ADD CONSTRAINT book_chapters_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;


--
-- Name: book_chunks book_chunks_book_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.book_chunks
    ADD CONSTRAINT book_chunks_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;


--
-- Name: chat_messages chat_messages_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id) ON DELETE CASCADE;


--
-- Name: chat_sessions chat_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: portfolio_holdings portfolio_holdings_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_holdings
    ADD CONSTRAINT portfolio_holdings_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: portfolio_items portfolio_items_portfolio_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_items
    ADD CONSTRAINT portfolio_items_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE CASCADE;


--
-- Name: portfolios portfolios_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: research_reports research_reports_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_reports
    ADD CONSTRAINT research_reports_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_bookmarks user_bookmarks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_bookmarks
    ADD CONSTRAINT user_bookmarks_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_credits user_credits_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_credits
    ADD CONSTRAINT user_credits_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_lesson_progress user_lesson_progress_lesson_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_lesson_progress
    ADD CONSTRAINT user_lesson_progress_lesson_id_fkey FOREIGN KEY (lesson_id) REFERENCES public.lessons(id) ON DELETE CASCADE;


--
-- Name: user_lesson_progress user_lesson_progress_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_lesson_progress
    ADD CONSTRAINT user_lesson_progress_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_study_schedules user_study_schedules_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_study_schedules
    ADD CONSTRAINT user_study_schedules_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: watchlist_items watchlist_items_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist_items
    ADD CONSTRAINT watchlist_items_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: whale_alerts whale_alerts_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_alerts
    ADD CONSTRAINT whale_alerts_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: whale_filing_snapshots whale_filing_snapshots_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_filing_snapshots
    ADD CONSTRAINT whale_filing_snapshots_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: whale_follows whale_follows_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_follows
    ADD CONSTRAINT whale_follows_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: whale_follows whale_follows_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_follows
    ADD CONSTRAINT whale_follows_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: whale_holdings whale_holdings_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_holdings
    ADD CONSTRAINT whale_holdings_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: whale_profile_cache whale_profile_cache_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_profile_cache
    ADD CONSTRAINT whale_profile_cache_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: whale_sector_allocations whale_sector_allocations_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_sector_allocations
    ADD CONSTRAINT whale_sector_allocations_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: whale_trade_groups whale_trade_groups_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_trade_groups
    ADD CONSTRAINT whale_trade_groups_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: whale_trades whale_trades_trade_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_trades
    ADD CONSTRAINT whale_trades_trade_group_id_fkey FOREIGN KEY (trade_group_id) REFERENCES public.whale_trade_groups(id) ON DELETE SET NULL;


--
-- Name: whale_trades whale_trades_whale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whale_trades
    ADD CONSTRAINT whale_trades_whale_id_fkey FOREIGN KEY (whale_id) REFERENCES public.whales(id) ON DELETE CASCADE;


--
-- Name: objects objects_bucketId_fkey; Type: FK CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.objects
    ADD CONSTRAINT "objects_bucketId_fkey" FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id);


--
-- Name: s3_multipart_uploads s3_multipart_uploads_bucket_id_fkey; Type: FK CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.s3_multipart_uploads
    ADD CONSTRAINT s3_multipart_uploads_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id);


--
-- Name: s3_multipart_uploads_parts s3_multipart_uploads_parts_bucket_id_fkey; Type: FK CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.s3_multipart_uploads_parts
    ADD CONSTRAINT s3_multipart_uploads_parts_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id);


--
-- Name: s3_multipart_uploads_parts s3_multipart_uploads_parts_upload_id_fkey; Type: FK CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.s3_multipart_uploads_parts
    ADD CONSTRAINT s3_multipart_uploads_parts_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES storage.s3_multipart_uploads(id) ON DELETE CASCADE;


--
-- Name: vector_indexes vector_indexes_bucket_id_fkey; Type: FK CONSTRAINT; Schema: storage; Owner: -
--

ALTER TABLE ONLY storage.vector_indexes
    ADD CONSTRAINT vector_indexes_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES storage.buckets_vectors(id);


--
-- Name: audit_log_entries; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.audit_log_entries ENABLE ROW LEVEL SECURITY;

--
-- Name: flow_state; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.flow_state ENABLE ROW LEVEL SECURITY;

--
-- Name: identities; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.identities ENABLE ROW LEVEL SECURITY;

--
-- Name: instances; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.instances ENABLE ROW LEVEL SECURITY;

--
-- Name: mfa_amr_claims; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.mfa_amr_claims ENABLE ROW LEVEL SECURITY;

--
-- Name: mfa_challenges; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.mfa_challenges ENABLE ROW LEVEL SECURITY;

--
-- Name: mfa_factors; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.mfa_factors ENABLE ROW LEVEL SECURITY;

--
-- Name: one_time_tokens; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.one_time_tokens ENABLE ROW LEVEL SECURITY;

--
-- Name: refresh_tokens; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.refresh_tokens ENABLE ROW LEVEL SECURITY;

--
-- Name: saml_providers; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.saml_providers ENABLE ROW LEVEL SECURITY;

--
-- Name: saml_relay_states; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.saml_relay_states ENABLE ROW LEVEL SECURITY;

--
-- Name: schema_migrations; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.schema_migrations ENABLE ROW LEVEL SECURITY;

--
-- Name: sessions; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: sso_domains; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.sso_domains ENABLE ROW LEVEL SECURITY;

--
-- Name: sso_providers; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.sso_providers ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: auth; Owner: -
--

ALTER TABLE auth.users ENABLE ROW LEVEL SECURITY;

--
-- Name: sector_benchmarks Allow public read access; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Allow public read access" ON public.sector_benchmarks FOR SELECT USING (true);


--
-- Name: sector_benchmarks Allow service_role full access; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Allow service_role full access" ON public.sector_benchmarks TO service_role USING (true) WITH CHECK (true);


--
-- Name: ticker_data_cache Service role full access; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access" ON public.ticker_data_cache USING (true) WITH CHECK (true);


--
-- Name: portfolio_holdings Service role full access on holdings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on holdings" ON public.portfolio_holdings TO service_role USING (true) WITH CHECK (true);


--
-- Name: portfolio_holdings Service role full access on portfolio_holdings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on portfolio_holdings" ON public.portfolio_holdings USING ((auth.role() = 'service_role'::text));


--
-- Name: portfolio_items Service role full access on portfolio_items; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on portfolio_items" ON public.portfolio_items TO service_role USING (true) WITH CHECK (true);


--
-- Name: portfolios Service role full access on portfolios; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on portfolios" ON public.portfolios TO service_role USING (true) WITH CHECK (true);


--
-- Name: watchlist_items Service role full access on watchlist; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on watchlist" ON public.watchlist_items TO service_role USING (true) WITH CHECK (true);


--
-- Name: portfolio_holdings Users can delete own holdings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own holdings" ON public.portfolio_holdings FOR DELETE USING ((user_id = auth.uid()));


--
-- Name: portfolio_holdings Users can insert own holdings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can insert own holdings" ON public.portfolio_holdings FOR INSERT WITH CHECK ((user_id = auth.uid()));


--
-- Name: portfolio_holdings Users can update own holdings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update own holdings" ON public.portfolio_holdings FOR UPDATE USING ((user_id = auth.uid()));


--
-- Name: portfolio_holdings Users can view own holdings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can view own holdings" ON public.portfolio_holdings FOR SELECT USING ((user_id = auth.uid()));


--
-- Name: portfolio_items Users manage own portfolio items; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users manage own portfolio items" ON public.portfolio_items USING ((EXISTS ( SELECT 1
   FROM public.portfolios p
  WHERE ((p.id = portfolio_items.portfolio_id) AND (p.user_id = auth.uid()))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.portfolios p
  WHERE ((p.id = portfolio_items.portfolio_id) AND (p.user_id = auth.uid())))));


--
-- Name: portfolios Users manage own portfolios; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users manage own portfolios" ON public.portfolios USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: agent_personas; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_personas ENABLE ROW LEVEL SECURITY;

--
-- Name: article_chunks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.article_chunks ENABLE ROW LEVEL SECURITY;

--
-- Name: article_chunks article_chunks_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY article_chunks_select_all ON public.article_chunks FOR SELECT USING (true);


--
-- Name: article_chunks article_chunks_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY article_chunks_service_all ON public.article_chunks USING ((auth.role() = 'service_role'::text));


--
-- Name: asset_snapshots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.asset_snapshots ENABLE ROW LEVEL SECURITY;

--
-- Name: book_chapters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.book_chapters ENABLE ROW LEVEL SECURITY;

--
-- Name: book_chapters book_chapters_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY book_chapters_select_all ON public.book_chapters FOR SELECT USING (true);


--
-- Name: book_chapters book_chapters_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY book_chapters_service_all ON public.book_chapters USING ((auth.role() = 'service_role'::text));


--
-- Name: book_chunks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.book_chunks ENABLE ROW LEVEL SECURITY;

--
-- Name: book_chunks book_chunks_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY book_chunks_select_all ON public.book_chunks FOR SELECT USING (true);


--
-- Name: book_chunks book_chunks_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY book_chunks_service_all ON public.book_chunks USING ((auth.role() = 'service_role'::text));


--
-- Name: user_bookmarks bookmarks_delete_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY bookmarks_delete_own ON public.user_bookmarks FOR DELETE USING ((auth.uid() = user_id));


--
-- Name: user_bookmarks bookmarks_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY bookmarks_insert_own ON public.user_bookmarks FOR INSERT WITH CHECK ((auth.uid() = user_id));


--
-- Name: user_bookmarks bookmarks_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY bookmarks_select_own ON public.user_bookmarks FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: books; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.books ENABLE ROW LEVEL SECURITY;

--
-- Name: books books_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY books_select_all ON public.books FOR SELECT USING (true);


--
-- Name: books books_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY books_service_all ON public.books USING ((auth.role() = 'service_role'::text));


--
-- Name: chat_messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;

--
-- Name: chat_messages chat_messages_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_messages_insert_own ON public.chat_messages FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.chat_sessions
  WHERE ((chat_sessions.id = chat_messages.session_id) AND (chat_sessions.user_id = auth.uid())))));


--
-- Name: chat_messages chat_messages_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_messages_select_own ON public.chat_messages FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.chat_sessions
  WHERE ((chat_sessions.id = chat_messages.session_id) AND (chat_sessions.user_id = auth.uid())))));


--
-- Name: chat_messages chat_messages_service_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_messages_service_insert ON public.chat_messages USING ((auth.role() = 'service_role'::text));


--
-- Name: chat_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.chat_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: chat_sessions chat_sessions_delete_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_sessions_delete_own ON public.chat_sessions FOR DELETE USING ((auth.uid() = user_id));


--
-- Name: chat_sessions chat_sessions_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_sessions_insert_own ON public.chat_sessions FOR INSERT WITH CHECK ((auth.uid() = user_id));


--
-- Name: chat_sessions chat_sessions_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_sessions_select_own ON public.chat_sessions FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: chat_sessions chat_sessions_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_sessions_service_all ON public.chat_sessions USING ((auth.role() = 'service_role'::text));


--
-- Name: chat_sessions chat_sessions_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY chat_sessions_update_own ON public.chat_sessions FOR UPDATE USING ((auth.uid() = user_id));


--
-- Name: company_filing_chunks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.company_filing_chunks ENABLE ROW LEVEL SECURITY;

--
-- Name: company_profile_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.company_profile_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: company_profile_cache company_profile_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY company_profile_cache_public_read ON public.company_profile_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: company_profile_cache company_profile_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY company_profile_cache_service_write ON public.company_profile_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: competitor_intel_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.competitor_intel_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: competitor_intel_audit competitor_intel_audit_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY competitor_intel_audit_public_read ON public.competitor_intel_audit FOR SELECT TO authenticated, anon USING (true);


--
-- Name: competitor_intel_audit competitor_intel_audit_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY competitor_intel_audit_service_write ON public.competitor_intel_audit TO service_role USING (true) WITH CHECK (true);


--
-- Name: competitor_intel_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.competitor_intel_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: competitor_intel_cache competitor_intel_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY competitor_intel_cache_public_read ON public.competitor_intel_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: competitor_intel_cache competitor_intel_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY competitor_intel_cache_service_write ON public.competitor_intel_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: user_credits credits_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credits_select_own ON public.user_credits FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: user_credits credits_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credits_service_all ON public.user_credits USING ((auth.role() = 'service_role'::text));


--
-- Name: user_credits credits_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credits_update_own ON public.user_credits FOR UPDATE USING ((auth.uid() = user_id));


--
-- Name: crypto_coin_id_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.crypto_coin_id_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: crypto_coin_id_cache crypto_coin_id_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY crypto_coin_id_cache_public_read ON public.crypto_coin_id_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: crypto_coin_id_cache crypto_coin_id_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY crypto_coin_id_cache_service_write ON public.crypto_coin_id_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: crypto_fundamentals_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.crypto_fundamentals_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: crypto_fundamentals_cache crypto_fundamentals_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY crypto_fundamentals_cache_public_read ON public.crypto_fundamentals_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: crypto_fundamentals_cache crypto_fundamentals_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY crypto_fundamentals_cache_service_write ON public.crypto_fundamentals_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: crypto_snapshots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.crypto_snapshots ENABLE ROW LEVEL SECURITY;

--
-- Name: crypto_snapshots crypto_snapshots_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY crypto_snapshots_public_read ON public.crypto_snapshots FOR SELECT TO authenticated, anon USING (true);


--
-- Name: crypto_snapshots crypto_snapshots_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY crypto_snapshots_service_write ON public.crypto_snapshots TO service_role USING (true) WITH CHECK (true);


--
-- Name: etf_detail_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.etf_detail_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: etf_detail_cache etf_detail_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY etf_detail_cache_public_read ON public.etf_detail_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: etf_detail_cache etf_detail_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY etf_detail_cache_service_write ON public.etf_detail_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: etf_snapshot_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.etf_snapshot_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: etf_snapshot_cache etf_snapshot_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY etf_snapshot_cache_public_read ON public.etf_snapshot_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: etf_snapshot_cache etf_snapshot_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY etf_snapshot_cache_service_write ON public.etf_snapshot_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: company_filing_chunks filing_chunks_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY filing_chunks_select_all ON public.company_filing_chunks FOR SELECT USING (true);


--
-- Name: company_filing_chunks filing_chunks_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY filing_chunks_service_all ON public.company_filing_chunks USING ((auth.role() = 'service_role'::text));


--
-- Name: geopolitical_macro_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.geopolitical_macro_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: geopolitical_macro_audit geopolitical_macro_audit_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY geopolitical_macro_audit_service_all ON public.geopolitical_macro_audit TO service_role USING (true) WITH CHECK (true);


--
-- Name: geopolitical_macro_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.geopolitical_macro_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: geopolitical_macro_cache geopolitical_macro_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY geopolitical_macro_cache_public_read ON public.geopolitical_macro_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: geopolitical_macro_cache geopolitical_macro_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY geopolitical_macro_cache_service_write ON public.geopolitical_macro_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: index_detail_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.index_detail_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: index_detail_cache index_detail_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY index_detail_cache_public_read ON public.index_detail_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: index_detail_cache index_detail_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY index_detail_cache_service_write ON public.index_detail_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: index_macro_forecast_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.index_macro_forecast_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: index_macro_forecast_cache index_macro_forecast_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY index_macro_forecast_cache_public_read ON public.index_macro_forecast_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: index_macro_forecast_cache index_macro_forecast_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY index_macro_forecast_cache_service_write ON public.index_macro_forecast_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: industry_dossier; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.industry_dossier ENABLE ROW LEVEL SECURITY;

--
-- Name: industry_dossier industry_dossier_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY industry_dossier_public_read ON public.industry_dossier FOR SELECT TO authenticated, anon USING (true);


--
-- Name: industry_dossier industry_dossier_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY industry_dossier_service_write ON public.industry_dossier TO service_role USING (true) WITH CHECK (true);


--
-- Name: industry_moat_benchmarks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.industry_moat_benchmarks ENABLE ROW LEVEL SECURITY;

--
-- Name: industry_moat_benchmarks industry_moat_benchmarks_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY industry_moat_benchmarks_public_read ON public.industry_moat_benchmarks FOR SELECT TO authenticated, anon USING (true);


--
-- Name: industry_moat_benchmarks industry_moat_benchmarks_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY industry_moat_benchmarks_service_write ON public.industry_moat_benchmarks TO service_role USING (true) WITH CHECK (true);


--
-- Name: industry_override_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.industry_override_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: industry_override_audit industry_override_audit_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY industry_override_audit_public_read ON public.industry_override_audit FOR SELECT TO authenticated, anon USING (true);


--
-- Name: industry_override_audit industry_override_audit_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY industry_override_audit_service_write ON public.industry_override_audit TO service_role USING (true) WITH CHECK (true);


--
-- Name: ip_intel_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ip_intel_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: ip_intel_audit ip_intel_audit_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ip_intel_audit_public_read ON public.ip_intel_audit FOR SELECT TO authenticated, anon USING (true);


--
-- Name: ip_intel_audit ip_intel_audit_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ip_intel_audit_service_write ON public.ip_intel_audit TO service_role USING (true) WITH CHECK (true);


--
-- Name: ip_intel_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ip_intel_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: ip_intel_cache ip_intel_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ip_intel_cache_public_read ON public.ip_intel_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: ip_intel_cache ip_intel_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ip_intel_cache_service_write ON public.ip_intel_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: user_lesson_progress lesson_progress_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lesson_progress_insert_own ON public.user_lesson_progress FOR INSERT WITH CHECK ((auth.uid() = user_id));


--
-- Name: user_lesson_progress lesson_progress_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lesson_progress_select_own ON public.user_lesson_progress FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: user_lesson_progress lesson_progress_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lesson_progress_service_all ON public.user_lesson_progress USING ((auth.role() = 'service_role'::text));


--
-- Name: user_lesson_progress lesson_progress_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lesson_progress_update_own ON public.user_lesson_progress FOR UPDATE USING ((auth.uid() = user_id));


--
-- Name: lessons; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lessons ENABLE ROW LEVEL SECURITY;

--
-- Name: lessons lessons_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lessons_select_all ON public.lessons FOR SELECT USING (true);


--
-- Name: lessons lessons_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY lessons_service_all ON public.lessons USING ((auth.role() = 'service_role'::text));


--
-- Name: market_deep_dive_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.market_deep_dive_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: market_deep_dive_cache market_deep_dive_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY market_deep_dive_cache_public_read ON public.market_deep_dive_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: market_deep_dive_cache market_deep_dive_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY market_deep_dive_cache_service_write ON public.market_deep_dive_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: moat_intel_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.moat_intel_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: moat_intel_audit moat_intel_audit_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY moat_intel_audit_public_read ON public.moat_intel_audit FOR SELECT TO authenticated, anon USING (true);


--
-- Name: moat_intel_audit moat_intel_audit_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY moat_intel_audit_service_write ON public.moat_intel_audit TO service_role USING (true) WITH CHECK (true);


--
-- Name: moat_intel_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.moat_intel_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: moat_intel_cache moat_intel_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY moat_intel_cache_public_read ON public.moat_intel_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: moat_intel_cache moat_intel_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY moat_intel_cache_service_write ON public.moat_intel_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: money_move_articles; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.money_move_articles ENABLE ROW LEVEL SECURITY;

--
-- Name: money_move_articles money_moves_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY money_moves_select_all ON public.money_move_articles FOR SELECT USING (true);


--
-- Name: money_move_articles money_moves_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY money_moves_service_all ON public.money_move_articles USING ((auth.role() = 'service_role'::text));


--
-- Name: news_articles; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.news_articles ENABLE ROW LEVEL SECURITY;

--
-- Name: news_articles news_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY news_select_all ON public.news_articles FOR SELECT USING (true);


--
-- Name: news_articles news_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY news_service_all ON public.news_articles USING ((auth.role() = 'service_role'::text));


--
-- Name: agent_personas personas_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY personas_select_all ON public.agent_personas FOR SELECT USING (true);


--
-- Name: agent_personas personas_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY personas_service_all ON public.agent_personas USING ((auth.role() = 'service_role'::text));


--
-- Name: portfolio_holdings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.portfolio_holdings ENABLE ROW LEVEL SECURITY;

--
-- Name: portfolio_items; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.portfolio_items ENABLE ROW LEVEL SECURITY;

--
-- Name: portfolio_items portfolio_items_owner; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY portfolio_items_owner ON public.portfolio_items USING ((portfolio_id IN ( SELECT portfolios.id
   FROM public.portfolios
  WHERE (portfolios.user_id = auth.uid()))));


--
-- Name: portfolios; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.portfolios ENABLE ROW LEVEL SECURITY;

--
-- Name: portfolios portfolios_owner; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY portfolios_owner ON public.portfolios USING ((user_id = auth.uid()));


--
-- Name: price_catalyst_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.price_catalyst_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: price_catalyst_audit price_catalyst_audit_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY price_catalyst_audit_service_all ON public.price_catalyst_audit TO service_role USING (true) WITH CHECK (true);


--
-- Name: price_catalyst_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.price_catalyst_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: price_catalyst_cache price_catalyst_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY price_catalyst_cache_public_read ON public.price_catalyst_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: price_catalyst_cache price_catalyst_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY price_catalyst_cache_service_write ON public.price_catalyst_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: research_reports reports_delete_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY reports_delete_own ON public.research_reports FOR DELETE USING ((auth.uid() = user_id));


--
-- Name: research_reports reports_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY reports_insert_own ON public.research_reports FOR INSERT WITH CHECK ((auth.uid() = user_id));


--
-- Name: research_reports reports_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY reports_select_own ON public.research_reports FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: research_reports reports_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY reports_service_all ON public.research_reports USING ((auth.role() = 'service_role'::text));


--
-- Name: research_reports reports_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY reports_update_own ON public.research_reports FOR UPDATE USING ((auth.uid() = user_id));


--
-- Name: research_reports; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.research_reports ENABLE ROW LEVEL SECURITY;

--
-- Name: sector_aggregates; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sector_aggregates ENABLE ROW LEVEL SECURITY;

--
-- Name: sector_aggregates sector_aggregates_read_authenticated; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sector_aggregates_read_authenticated ON public.sector_aggregates FOR SELECT TO authenticated USING (true);


--
-- Name: sector_benchmarks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sector_benchmarks ENABLE ROW LEVEL SECURITY;

--
-- Name: short_interest_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.short_interest_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: short_interest_cache short_interest_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY short_interest_cache_public_read ON public.short_interest_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: short_interest_cache short_interest_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY short_interest_cache_service_write ON public.short_interest_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: signals_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.signals_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: signals_cache signals_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY signals_cache_public_read ON public.signals_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: signals_cache signals_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY signals_cache_service_write ON public.signals_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: snapshot_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.snapshot_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: snapshot_cache snapshot_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY snapshot_cache_public_read ON public.snapshot_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: snapshot_cache snapshot_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY snapshot_cache_service_write ON public.snapshot_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: asset_snapshots snapshots_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY snapshots_select_all ON public.asset_snapshots FOR SELECT USING (true);


--
-- Name: asset_snapshots snapshots_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY snapshots_service_all ON public.asset_snapshots USING ((auth.role() = 'service_role'::text));


--
-- Name: stock_fundamentals_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.stock_fundamentals_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: stock_fundamentals_cache stock_fundamentals_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY stock_fundamentals_cache_public_read ON public.stock_fundamentals_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: stock_fundamentals_cache stock_fundamentals_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY stock_fundamentals_cache_service_write ON public.stock_fundamentals_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: user_study_schedules study_schedules_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY study_schedules_insert_own ON public.user_study_schedules FOR INSERT WITH CHECK ((auth.uid() = user_id));


--
-- Name: user_study_schedules study_schedules_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY study_schedules_select_own ON public.user_study_schedules FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: user_study_schedules study_schedules_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY study_schedules_service_all ON public.user_study_schedules USING ((auth.role() = 'service_role'::text));


--
-- Name: user_study_schedules study_schedules_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY study_schedules_update_own ON public.user_study_schedules FOR UPDATE USING ((auth.uid() = user_id));


--
-- Name: ticker_data_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ticker_data_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: ticker_news_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ticker_news_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: ticker_news_cache ticker_news_cache_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ticker_news_cache_service_all ON public.ticker_news_cache USING ((auth.role() = 'service_role'::text));


--
-- Name: ticker_report_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ticker_report_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: ticker_report_cache ticker_report_cache_public_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ticker_report_cache_public_read ON public.ticker_report_cache FOR SELECT TO authenticated, anon USING (true);


--
-- Name: ticker_report_cache ticker_report_cache_service_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ticker_report_cache_service_write ON public.ticker_report_cache TO service_role USING (true) WITH CHECK (true);


--
-- Name: user_book_progress; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_book_progress ENABLE ROW LEVEL SECURITY;

--
-- Name: user_book_progress user_book_progress_delete_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_book_progress_delete_own ON public.user_book_progress FOR DELETE TO authenticated USING ((auth.uid() = user_id));


--
-- Name: user_book_progress user_book_progress_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_book_progress_insert_own ON public.user_book_progress FOR INSERT TO authenticated WITH CHECK ((auth.uid() = user_id));


--
-- Name: user_book_progress user_book_progress_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_book_progress_select_own ON public.user_book_progress FOR SELECT TO authenticated USING ((auth.uid() = user_id));


--
-- Name: user_book_progress user_book_progress_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_book_progress_service_all ON public.user_book_progress TO service_role USING (true) WITH CHECK (true);


--
-- Name: user_book_progress user_book_progress_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_book_progress_update_own ON public.user_book_progress FOR UPDATE TO authenticated USING ((auth.uid() = user_id));


--
-- Name: user_bookmarks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_bookmarks ENABLE ROW LEVEL SECURITY;

--
-- Name: user_credits; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_credits ENABLE ROW LEVEL SECURITY;

--
-- Name: user_learn_progress; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_learn_progress ENABLE ROW LEVEL SECURITY;

--
-- Name: user_learn_progress user_learn_progress_delete_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_learn_progress_delete_own ON public.user_learn_progress FOR DELETE TO authenticated USING ((auth.uid() = user_id));


--
-- Name: user_learn_progress user_learn_progress_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_learn_progress_insert_own ON public.user_learn_progress FOR INSERT TO authenticated WITH CHECK ((auth.uid() = user_id));


--
-- Name: user_learn_progress user_learn_progress_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_learn_progress_select_own ON public.user_learn_progress FOR SELECT TO authenticated USING ((auth.uid() = user_id));


--
-- Name: user_learn_progress user_learn_progress_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_learn_progress_service_all ON public.user_learn_progress TO service_role USING (true) WITH CHECK (true);


--
-- Name: user_learn_progress user_learn_progress_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_learn_progress_update_own ON public.user_learn_progress FOR UPDATE TO authenticated USING ((auth.uid() = user_id));


--
-- Name: user_lesson_progress; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_lesson_progress ENABLE ROW LEVEL SECURITY;

--
-- Name: user_study_schedules; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_study_schedules ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- Name: users users_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_insert_own ON public.users FOR INSERT WITH CHECK ((auth.uid() = id));


--
-- Name: users users_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_select_own ON public.users FOR SELECT USING ((auth.uid() = id));


--
-- Name: users users_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_service_all ON public.users USING ((auth.role() = 'service_role'::text));


--
-- Name: users users_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_update_own ON public.users FOR UPDATE USING ((auth.uid() = id));


--
-- Name: watchlist_items watchlist_delete_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY watchlist_delete_own ON public.watchlist_items FOR DELETE USING ((auth.uid() = user_id));


--
-- Name: watchlist_items watchlist_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY watchlist_insert_own ON public.watchlist_items FOR INSERT WITH CHECK ((auth.uid() = user_id));


--
-- Name: watchlist_items; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.watchlist_items ENABLE ROW LEVEL SECURITY;

--
-- Name: watchlist_items watchlist_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY watchlist_select_own ON public.watchlist_items FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: watchlist_items watchlist_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY watchlist_service_all ON public.watchlist_items USING ((auth.role() = 'service_role'::text));


--
-- Name: watchlist_items watchlist_update_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY watchlist_update_own ON public.watchlist_items FOR UPDATE USING ((auth.uid() = user_id));


--
-- Name: whale_alerts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_alerts ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_alerts whale_alerts_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_alerts_select_all ON public.whale_alerts FOR SELECT USING (true);


--
-- Name: whale_alerts whale_alerts_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_alerts_service_all ON public.whale_alerts USING ((auth.role() = 'service_role'::text));


--
-- Name: whale_filing_snapshots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_filing_snapshots ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_filing_snapshots whale_filing_snapshots_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_filing_snapshots_select_all ON public.whale_filing_snapshots FOR SELECT USING (true);


--
-- Name: whale_filing_snapshots whale_filing_snapshots_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_filing_snapshots_service_all ON public.whale_filing_snapshots USING ((auth.role() = 'service_role'::text));


--
-- Name: whale_follows; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_follows ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_follows whale_follows_delete_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_follows_delete_own ON public.whale_follows FOR DELETE USING ((auth.uid() = user_id));


--
-- Name: whale_follows whale_follows_insert_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_follows_insert_own ON public.whale_follows FOR INSERT WITH CHECK ((auth.uid() = user_id));


--
-- Name: whale_follows whale_follows_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_follows_select_own ON public.whale_follows FOR SELECT USING ((auth.uid() = user_id));


--
-- Name: whale_holdings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_holdings ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_holdings whale_holdings_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_holdings_select_all ON public.whale_holdings FOR SELECT USING (true);


--
-- Name: whale_holdings whale_holdings_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_holdings_service_all ON public.whale_holdings USING ((auth.role() = 'service_role'::text));


--
-- Name: whale_profile_cache; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_profile_cache ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_profile_cache whale_profile_cache_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_profile_cache_select_all ON public.whale_profile_cache FOR SELECT USING (true);


--
-- Name: whale_profile_cache whale_profile_cache_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_profile_cache_service_all ON public.whale_profile_cache USING ((auth.role() = 'service_role'::text));


--
-- Name: whale_sector_allocations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_sector_allocations ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_sector_allocations whale_sectors_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_sectors_select_all ON public.whale_sector_allocations FOR SELECT USING (true);


--
-- Name: whale_sector_allocations whale_sectors_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_sectors_service_all ON public.whale_sector_allocations USING ((auth.role() = 'service_role'::text));


--
-- Name: whale_trade_groups; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_trade_groups ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_trade_groups whale_trade_groups_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_trade_groups_select_all ON public.whale_trade_groups FOR SELECT USING (true);


--
-- Name: whale_trade_groups whale_trade_groups_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_trade_groups_service_all ON public.whale_trade_groups USING ((auth.role() = 'service_role'::text));


--
-- Name: whale_trades; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whale_trades ENABLE ROW LEVEL SECURITY;

--
-- Name: whale_trades whale_trades_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_trades_select_all ON public.whale_trades FOR SELECT USING (true);


--
-- Name: whale_trades whale_trades_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whale_trades_service_all ON public.whale_trades USING ((auth.role() = 'service_role'::text));


--
-- Name: whales; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whales ENABLE ROW LEVEL SECURITY;

--
-- Name: whales whales_select_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whales_select_all ON public.whales FOR SELECT USING (true);


--
-- Name: whales whales_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY whales_service_all ON public.whales USING ((auth.role() = 'service_role'::text));


--
-- Name: messages; Type: ROW SECURITY; Schema: realtime; Owner: -
--

ALTER TABLE realtime.messages ENABLE ROW LEVEL SECURITY;

--
-- Name: objects book_media_public_read; Type: POLICY; Schema: storage; Owner: -
--

CREATE POLICY book_media_public_read ON storage.objects FOR SELECT TO authenticated, anon USING ((bucket_id = 'book-media'::text));


--
-- Name: objects book_media_service_write; Type: POLICY; Schema: storage; Owner: -
--

CREATE POLICY book_media_service_write ON storage.objects TO service_role USING ((bucket_id = 'book-media'::text)) WITH CHECK ((bucket_id = 'book-media'::text));


--
-- Name: buckets; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.buckets ENABLE ROW LEVEL SECURITY;

--
-- Name: buckets_analytics; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.buckets_analytics ENABLE ROW LEVEL SECURITY;

--
-- Name: buckets_vectors; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.buckets_vectors ENABLE ROW LEVEL SECURITY;

--
-- Name: objects journey_media_public_read; Type: POLICY; Schema: storage; Owner: -
--

CREATE POLICY journey_media_public_read ON storage.objects FOR SELECT TO authenticated, anon USING ((bucket_id = 'journey-media'::text));


--
-- Name: objects journey_media_service_write; Type: POLICY; Schema: storage; Owner: -
--

CREATE POLICY journey_media_service_write ON storage.objects TO service_role USING ((bucket_id = 'journey-media'::text)) WITH CHECK ((bucket_id = 'journey-media'::text));


--
-- Name: migrations; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.migrations ENABLE ROW LEVEL SECURITY;

--
-- Name: objects money_moves_media_public_read; Type: POLICY; Schema: storage; Owner: -
--

CREATE POLICY money_moves_media_public_read ON storage.objects FOR SELECT TO authenticated, anon USING ((bucket_id = 'money-moves-media'::text));


--
-- Name: objects money_moves_media_service_write; Type: POLICY; Schema: storage; Owner: -
--

CREATE POLICY money_moves_media_service_write ON storage.objects TO service_role USING ((bucket_id = 'money-moves-media'::text)) WITH CHECK ((bucket_id = 'money-moves-media'::text));


--
-- Name: objects; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;

--
-- Name: objects research_pdfs_service_all; Type: POLICY; Schema: storage; Owner: -
--

CREATE POLICY research_pdfs_service_all ON storage.objects TO service_role USING ((bucket_id = 'research-pdfs'::text)) WITH CHECK ((bucket_id = 'research-pdfs'::text));


--
-- Name: s3_multipart_uploads; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.s3_multipart_uploads ENABLE ROW LEVEL SECURITY;

--
-- Name: s3_multipart_uploads_parts; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.s3_multipart_uploads_parts ENABLE ROW LEVEL SECURITY;

--
-- Name: vector_indexes; Type: ROW SECURITY; Schema: storage; Owner: -
--

ALTER TABLE storage.vector_indexes ENABLE ROW LEVEL SECURITY;

--
-- Name: supabase_realtime; Type: PUBLICATION; Schema: -; Owner: -
--

CREATE PUBLICATION supabase_realtime WITH (publish = 'insert, update, delete, truncate');


--
-- Name: issue_graphql_placeholder; Type: EVENT TRIGGER; Schema: -; Owner: -
--

CREATE EVENT TRIGGER issue_graphql_placeholder ON sql_drop
         WHEN TAG IN ('DROP EXTENSION')
   EXECUTE FUNCTION extensions.set_graphql_placeholder();


--
-- Name: issue_pg_cron_access; Type: EVENT TRIGGER; Schema: -; Owner: -
--

CREATE EVENT TRIGGER issue_pg_cron_access ON ddl_command_end
         WHEN TAG IN ('CREATE EXTENSION')
   EXECUTE FUNCTION extensions.grant_pg_cron_access();


--
-- Name: issue_pg_graphql_access; Type: EVENT TRIGGER; Schema: -; Owner: -
--

CREATE EVENT TRIGGER issue_pg_graphql_access ON ddl_command_end
         WHEN TAG IN ('CREATE FUNCTION')
   EXECUTE FUNCTION extensions.grant_pg_graphql_access();


--
-- Name: issue_pg_net_access; Type: EVENT TRIGGER; Schema: -; Owner: -
--

CREATE EVENT TRIGGER issue_pg_net_access ON ddl_command_end
         WHEN TAG IN ('CREATE EXTENSION')
   EXECUTE FUNCTION extensions.grant_pg_net_access();


--
-- Name: pgrst_ddl_watch; Type: EVENT TRIGGER; Schema: -; Owner: -
--

CREATE EVENT TRIGGER pgrst_ddl_watch ON ddl_command_end
   EXECUTE FUNCTION extensions.pgrst_ddl_watch();


--
-- Name: pgrst_drop_watch; Type: EVENT TRIGGER; Schema: -; Owner: -
--

CREATE EVENT TRIGGER pgrst_drop_watch ON sql_drop
   EXECUTE FUNCTION extensions.pgrst_drop_watch();


--
-- PostgreSQL database dump complete
--

\unrestrict nRBbQvGZJy4alljx6HpiTBLhjCC4SWwzgNSprhX16CqJz9V4KHWQRrySPUN8DSB

