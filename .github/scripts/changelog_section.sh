#!/bin/bash
# Print the CHANGELOG.md section of one version, without its heading.
# Usage: changelog_section.sh 1.1.0 [CHANGELOG.md]
set -euo pipefail

version="$1"
file="${2:-CHANGELOG.md}"

section=$(awk -v heading="## ${version}" '
    $0 == heading { found = 1; next }
    found && /^## / { exit }
    found { print }
' "$file")

# Drop blank lines at the start and end of the section.
section=$(printf '%s\n' "$section" | sed -e '/./,$!d' | sed -e ':a' -e '/^\n*$/{$d;N;ba' -e '}')

if [ -z "$section" ]; then
    echo "No section '## ${version}' found in ${file}" >&2
    exit 1
fi

printf '%s\n' "$section"
