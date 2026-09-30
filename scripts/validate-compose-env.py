#!/usr/bin/env python3
"""Validate that every ${VAR} referenced in docker-compose.yml is
documented in .env.example and present in .github/test.env.
Usage: validate-compose-env.py [compose_file] [example_file] [test_env_file]"""

import re
import sys
from pathlib import Path

compose_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docker-compose.yml")
example_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".env.example")
test_env_file = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(".github/test.env")

if not (compose_file.is_file() and example_file.is_file()
        and test_env_file.is_file()):
    print("Required Compose validation input is missing", file=sys.stderr)
    print(f"compose={compose_file} example={example_file} "
          f"test_env={test_env_file}", file=sys.stderr)
    sys.exit(1)

# Match ${VAR} or $VAR but not $$ (escaped dollar)
ref_re = re.compile(r"(?<!\$)\$\{([A-Z_][A-Z0-9_]*)\}|\$(?!\$)([A-Z_][A-Z0-9_]*)")
referenced = sorted({m.group(1) or m.group(2)
                     for m in ref_re.finditer(compose_file.read_text())})


def env_keys(path: Path) -> set[str]:
    return {m.group(1) for m in
            re.finditer(r"^([A-Z_][A-Z0-9_]*)=", path.read_text(),
                        re.MULTILINE)}


documented = env_keys(example_file)
test_env = env_keys(test_env_file)

undocumented = sorted(set(referenced) - documented)
missing = sorted(set(referenced) - test_env)

if undocumented or missing:
    if undocumented:
        print(f"Referenced variables missing from {example_file}:",
              file=sys.stderr)
        print("\n".join(undocumented), file=sys.stderr)
    if missing:
        print(f"Referenced variables missing from {test_env_file}:",
              file=sys.stderr)
        print("\n".join(missing), file=sys.stderr)
    sys.exit(1)

print("All referenced Compose variables are documented and present "
      f"in {test_env_file}.")
