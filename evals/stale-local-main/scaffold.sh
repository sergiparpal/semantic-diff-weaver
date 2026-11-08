#!/usr/bin/env bash
# The repository this case reviews: fixture.sh's `stale-main` variant.
exec bash "$(dirname "${BASH_SOURCE[0]}")/../fixture.sh" stale-main
