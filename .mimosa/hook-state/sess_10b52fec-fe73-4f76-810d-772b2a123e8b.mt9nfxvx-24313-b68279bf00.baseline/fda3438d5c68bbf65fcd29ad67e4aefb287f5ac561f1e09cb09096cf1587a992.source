#!/usr/bin/env python3
"""Validate docker-compose.yml environment variables"""

import re
import sys

def extract_vars(content):
    """Extract ${VAR} and $VAR but not $$ (escaped dollar)"""
    # First replace $$ with placeholder to avoid matching inside it
    content = content.replace('$$', '__DOLLAR__')
    # Match ${VAR} or $VAR
    pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}|\$([A-Z_][A-Z0-9_]*)'
    matches = re.findall(pattern, content)
    # Flatten and filter
    vars = []
    for m in matches:
        vars.append(m[0] if m[0] else m[1])
    return set(vars)

def read_vars_from_env(filepath):
    """Read variable names from .env file"""
    vars = set()
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                var = line.split('=', 1)[0].strip()
                if var:
                    vars.add(var)
    return vars

def main():
    compose_file = sys.argv[1] if len(sys.argv) > 1 else 'docker-compose.yml'
    example_file = sys.argv[2] if len(sys.argv) > 2 else '.env.example'
    test_env_file = sys.argv[3] if len(sys.argv) > 3 else '.github/test.env'

    for f in [compose_file, example_file, test_env_file]:
        try:
            open(f).close()
        except FileNotFoundError:
            print(f"Required file not found: {f}", file=sys.stderr)
            return 1

    with open(compose_file) as f:
        compose_content = f.read()

    referenced = extract_vars(compose_content)
    documented = read_vars_from_env(example_file)
    test_env = read_vars_from_env(test_env_file)

    undocumented = referenced - documented
    missing = referenced - test_env

    if undocumented or missing:
        if undocumented:
            print(f"Referenced variables missing from {example_file}:", file=sys.stderr)
            for v in sorted(undocumented):
                print(f"  {v}", file=sys.stderr)
        if missing:
            print(f"Referenced variables missing from {test_env_file}:", file=sys.stderr)
            for v in sorted(missing):
                print(f"  {v}", file=sys.stderr)
        return 1

    print(f"All referenced Compose variables are documented and present in {test_env_file}.")
    return 0

if __name__ == '__main__':
    sys.exit(main())