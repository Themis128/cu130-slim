import sys
import re
for f in sys.argv[1:]:
    with open(f, 'r') as fp:
        lines = fp.readlines()
    # ensure --- at start
    if not lines[0].startswith('---'):
        lines.insert(0, '---\n')
    # quote on: line that after stripping is 'on:'
    for i, line in enumerate(lines):
        if line.strip() == 'on:' and not (line.startswith('"') and line.endswith('"\n')):
            lines[i] = '"on":\n'
    # remove spaces inside brackets
    for i, line in enumerate(lines):
        lines[i] = re.sub(r'\[\s+([^]]*?)\s+\]', r'[\1]', line)
    # trim trailing spaces
    lines = [line.rstrip() + '\n' for line in lines]
    # ensure newline at end
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    with open(f, 'w') as fp:
        fp.writelines(lines)
