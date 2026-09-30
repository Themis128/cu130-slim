#!/usr/bin/env python3
"""Update LinkedIn personal About section via browser sidecar.
Usage: li-personal-update-about.py "About text" OR li-personal-update-about.py /path/to/file.txt
Note: LinkedIn uses a contenteditable div (not textarea) for the About section."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _li_org_update import evaluate, post  # noqa: E402
from skill_http import usage  # noqa: E402

val = sys.argv[1] if len(sys.argv) > 1 else usage('li-personal-update-about.py "About text"')
if Path(val).is_file():
    about = Path(val).read_text().strip()
else:
    about = val

print(f"→ Updating LinkedIn personal About ({len(about)} chars)...")
post("/debug/navigate", {"url": "https://www.linkedin.com/in/baltzakis-themis/"})
time.sleep(5)

print(f'  Edit button: {evaluate("""(async () => {
    const btns = document.querySelectorAll(\'button, a\');
    for (const b of btns) {
        const aria = b.getAttribute(\'aria-label\') || \'\';
        if (aria === \'Edit about\') { b.click(); await new Promise(r => setTimeout(r, 3000)); return \'clicked\'; }
    }
    return \'not found\';
})()""", 15)}')

set_js = f"""
(async () => {{
    const editor = document.querySelector('div[contenteditable="true"][role="textbox"]');
    if (!editor) {{
        const ta = document.querySelector('textarea');
        if (ta) {{
            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(ta, {json.dumps(about)});
            ta.dispatchEvent(new Event('input', {{bubbles: true}}));
            return 'textarea set: ' + ta.value.length + ' chars';
        }}
        return 'no editor found';
    }}

    editor.focus();
    await new Promise(r => setTimeout(r, 500));

    const range = document.createRange();
    range.selectNodeContents(editor);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    await new Promise(r => setTimeout(r, 500));

    document.execCommand('delete');
    await new Promise(r => setTimeout(r, 500));

    document.execCommand('insertText', false, {json.dumps(about)});
    await new Promise(r => setTimeout(r, 1000));

    editor.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: {json.dumps(about)}}}));

    return 'contenteditable set: ' + editor.textContent.length + ' chars';
}})()
"""
print(f"  Set: {evaluate(set_js, 15)}")
time.sleep(2)

print(f'  Save: {evaluate("""(async () => {
    const btns = document.querySelectorAll(\'button\');
    for (const b of btns) {
        if (b.textContent.trim() === \'Save\') { b.click(); await new Promise(r => setTimeout(r, 5000)); return \'saved\'; }
    }
    return \'no save button\';
})()""", 30)}')
