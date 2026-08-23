-- Mealwise schema. Apply with:  psql "$DATABASE_URL" -f schema.sql
--
-- Identity comes from Swiggy: the access token is a JWT whose `sub` is stable per
-- account, so "Connect Swiggy" IS the sign-in. Everything scopes by users.id.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  swiggy_sub  text NOT NULL UNIQUE,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Swiggy credentials, ENCRYPTED at rest (AES-256-GCM, see mealwise/crypto.py). These
-- place real orders, so a read-only database leak must not be enough to spend money.
-- Access tokens last ~5 days; the AS advertises refresh_token, so we store one if issued.
CREATE TABLE IF NOT EXISTS swiggy_tokens (
  user_id           uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  access_token_enc  text NOT NULL,
  refresh_token_enc text,
  expires_at        timestamptz NOT NULL,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profiles (
  user_id               uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  age_years             integer,
  sex                   text,
  height_cm             integer,
  weight_kg             numeric(5,2),
  goal                  text,
  diet                  text,
  allergies             jsonb NOT NULL DEFAULT '[]'::jsonb,
  dislikes              jsonb NOT NULL DEFAULT '[]'::jsonb,
  budget_inr            integer,
  budget_period         text DEFAULT 'monthly',
  target_kcal           integer,
  target_protein_g      integer,
  target_carbs_g        integer,
  target_fat_g          integer,
  target_veg_servings   integer DEFAULT 4,
  target_fruit_servings integer DEFAULT 2,
  default_address_id    text,
  updated_at            timestamptz NOT NULL DEFAULT now()
);

-- total_inr is an integer because the API returns display strings ("₹310"), parsed once
-- on import. ordered_on is a DATE, not a timestamp: Food's `orderedTime` is
-- "August 22, 10:13 PM" with NO YEAR, so the exact instant is not recoverable and
-- pretending otherwise would be false precision.
CREATE TABLE IF NOT EXISTS orders (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source           text NOT NULL,
  external_id      text NOT NULL,
  ordered_on       date,
  ordered_time_raw text,
  total_inr        integer,
  status           text,
  merchant         text,
  via_mealwise     boolean NOT NULL DEFAULT false,
  raw              jsonb,
  imported_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, source, external_id)
);

-- Food history gives ONE joined string with no per-item price, so price_inr is usually
-- null. Order-level totals are the only reliable money figure.
CREATE TABLE IF NOT EXISTS order_items (
  id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id  uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  name      text NOT NULL,
  quantity  integer NOT NULL DEFAULT 1,
  price_inr integer
);

-- Nutrition is ESTIMATED by an LLM from the item name. Cached per normalised name so the
-- same dish is not re-billed on every dashboard load.
CREATE TABLE IF NOT EXISTS nutrition_estimates (
  item_key       text PRIMARY KEY,
  kcal           integer,
  protein_g      numeric(6,2),
  carbs_g        numeric(6,2),
  fat_g          numeric(6,2),
  veg_servings   numeric(4,2),
  fruit_servings numeric(4,2),
  confidence     text,
  model          text,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- What the agent proposed, and whether it became an order.
CREATE TABLE IF NOT EXISTS recommendations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at    timestamptz NOT NULL DEFAULT now(),
  options       jsonb,
  reason        text,
  chosen_index  integer,
  order_id      uuid REFERENCES orders(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS orders_user_date_idx ON orders (user_id, ordered_on);
CREATE INDEX IF NOT EXISTS order_items_order_idx ON order_items (order_id);
