import sys
for f in sys.argv[1:]:
    with open(f, 'r') as fp:
        lines = fp.readlines()
    # Find steps block under jobs.build
    in_steps = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith('steps:'):
            in_steps = True
            continue
        if in_steps:
            # If we hit a line that is less indented than steps and not empty, we left steps block
            if line.strip() == '':
                continue
            # Determine indentation of steps line
            # We'll just assume we are in steps until we see a line with same indentation as 'jobs:'?
            # Simpler: stop when we see a line that starts with word and colon at same indent as jobs?
            # We'll just process whole file and rely on pattern: after a line that starts with '      - ' we increase expected indent for following lines until next '      - ' or less indent.
            pass
    # Instead, do simple: for each line, if it starts with exactly six spaces and not a dash, add two spaces
    new_lines = []
    for line in lines:
        if line.startswith('      ') and not line.startswith('      - '):
            line = '  ' + line
        new_lines.append(line)
    with open(f, 'w') as fp:
        fp.writelines(new_lines)
