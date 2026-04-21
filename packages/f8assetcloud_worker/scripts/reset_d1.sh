#!/bin/sh

set -eu

usage() {
  echo "Usage: sh ./scripts/reset_d1.sh [remote|preview]" >&2
  echo "Optional overrides: D1_DATABASE_NAME, WRANGLER_TOML_PATH" >&2
}

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

target="$1"
preview_flag=""
migrate_script="d1:migrate"
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(CDPATH= cd -- "$script_dir/.." && pwd)"
wrangler_toml_path="${WRANGLER_TOML_PATH:-$project_dir/wrangler.toml}"
database_name="${D1_DATABASE_NAME:-}"

case "$target" in
  remote)
    ;;
  preview)
    preview_flag="--preview"
    migrate_script="d1:migrate:preview"
    ;;
  *)
    usage
    exit 1
    ;;
esac

if [ -z "$database_name" ]; then
  if [ ! -f "$wrangler_toml_path" ]; then
    echo "wrangler.toml not found: $wrangler_toml_path" >&2
    exit 1
  fi

  database_name="$(
    awk -F '=' '
      /^[[:space:]]*\[\[d1_databases\]\][[:space:]]*$/ {
        in_d1_block = 1
        next
      }
      in_d1_block && /^[[:space:]]*database_name[[:space:]]*=/ {
        value = $2
        gsub(/^[[:space:]]+/, "", value)
        gsub(/[[:space:]]+$/, "", value)
        gsub(/^"/, "", value)
        gsub(/"$/, "", value)
        print value
        exit
      }
    ' "$wrangler_toml_path"
  )"
fi

if [ -z "$database_name" ]; then
  echo "Failed to resolve d1 database_name from $wrangler_toml_path" >&2
  exit 1
fi

drop_sql='
PRAGMA foreign_keys = off;
DROP TABLE IF EXISTS asset_subscriptions;
DROP TABLE IF EXISTS asset_versions;
DROP TABLE IF EXISTS variant_details;
DROP TABLE IF EXISTS asset_heads;
DROP TABLE IF EXISTS bootstrap_admin_state;
DROP TABLE IF EXISTS site_settings;
DROP TABLE IF EXISTS session;
DROP TABLE IF EXISTS account;
DROP TABLE IF EXISTS verification;
DROP TABLE IF EXISTS user;
DROP TABLE IF EXISTS d1_migrations;
DROP TABLE IF EXISTS desktop_authorization_codes;
PRAGMA foreign_keys = on;
'

npx wrangler d1 execute "$database_name" --remote $preview_flag --command="$drop_sql"
npm run "$migrate_script"
