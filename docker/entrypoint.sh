#!/bin/sh
# Runs schema migrations (serialized via a Postgres advisory lock inside
# gca-migrate) before handing off to the service command.
set -e

gca-migrate

exec "$@"
