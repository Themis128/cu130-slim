---
name: messenger-e2ee
description: >-
  Handle end-to-end encrypted (E2EE) conversations in personal Facebook
  Messenger via the browser bridge. Detects the E2EE PIN entry dialog
  when opening encrypted conversations and enters the PIN automatically.
  Covers _handle_e2ee_pin_dialog and _navigate_to_thread E2EE handling
  on BrowserBridgeClient. Use when reading or sending messages to E2EE
  conversations, debugging PIN entry failures, or setting the
  MESSENGER_E2EE_PIN environment variable.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# Messenger E2EE (End-to-End Encrypted Conversations)

Handle end-to-end encrypted (E2EE) conversations in personal Facebook
Messenger. E2EE threads use a different URL path (`/messages/e2ee/t/` instead
of `/messages/t/`) and may show a PIN entry dialog when opening for the
first time.

## When to use

- Read messages from an E2EE conversation
- Send messages to an E2EE conversation
- Debug PIN entry dialog issues
- Set up the MESSENGER_E2EE_PIN environment variable
- Debug E2EE conversations not loading in the browser bridge

## How E2EE works in Messenger

Facebook Messenger E2EE conversations:
- Use URL path `/messages/e2ee/t/{thread_id}/` (vs `/messages/t/{thread_id}/`)
- May require a 6-digit PIN to decrypt messages (shown on first open)
- The PIN is set by the user in Messenger settings
- Once entered, the browser remembers the PIN for the session

## API methods

All methods are on `BrowserBridgeClient` in
`social-automation/backend/app/services/browser_bridge.py`.

### _navigate_to_thread(thread_id, is_e2ee=False)

Navigates to a specific Messenger thread. For E2EE threads, also calls
the PIN dialog handler automatically.

```python
bridge = BrowserBridgeClient("http://browser-novnc:9223")
# Navigate to an E2EE thread (PIN dialog handled automatically)
await bridge._navigate_to_thread("1234567890", is_e2ee=True)
```

### _handle_e2ee_pin_dialog(pin=None)

Detects the E2EE PIN entry dialog and enters the PIN if provided.

```python
# Check for PIN dialog (without entering PIN)
found = await bridge._handle_e2ee_pin_dialog()
# Returns: True if dialog found, False if no dialog

# Check for PIN dialog and enter PIN
found = await bridge._handle_e2ee_pin_dialog(pin="123456")
# Returns: True if dialog found and PIN entered
```

### Reading E2EE messages

```python
# Read messages from an E2EE conversation
result = await bridge.get_personal_messenger_messages_fast(
    thread_id="1234567890",
    is_e2ee=True,
)
```

### Sending to E2EE conversations

```python
# Send a message to an E2EE conversation
result = await bridge.send_personal_messenger_message(
    thread_id="1234567890",
    text="Hello!",
    is_e2ee=True,
)
```

## PIN dialog detection

The handler looks for the PIN dialog using multiple selectors:

```javascript
// Dialog container
'div[role="dialog"]'
'div[aria-label*="PIN"]'
'div[aria-label*="pin"]'
'div:has(input[type="password"][placeholder*="PIN"])'

// PIN input
'input[type="password"]'
'input[placeholder*="PIN"]'
'input[placeholder*="pin"]'
'input[autocomplete="off"][maxlength="6"]'
```

## Setting the E2EE PIN

```bash
# Set in .env (or docker-compose.yml environment)
MESSENGER_E2EE_PIN=123456

# Or pass directly to the handler
await bridge._handle_e2ee_pin_dialog(pin="123456")
```

## E2EE conversation detection

E2EE conversations are detected by URL path:

```python
# In get_personal_messenger_conversations_fast():
const match = href.match(/messages\/(?:e2ee\/)?t\/([0-9]+)/);
const isE2EE = href.includes('/e2ee/');
```

The `e2ee: true` flag is set on E2EE conversations in the conversation list.

## Integration with polling task

The `poll-personal-messenger` Celery task automatically handles E2EE:

```python
# In personal_messenger.py
for convo in threadable[:20]:
    is_e2ee = convo.get("e2ee", False)
    msgs_result = await bridge.get_personal_messenger_messages_fast(
        thread_id, is_e2ee=is_e2ee
    )
```

## Common issues

| Issue | Cause | Fix |
|-------|-------|-----|
| E2EE messages not loading | PIN dialog not handled | Set `MESSENGER_E2EE_PIN` env var |
| "PIN dialog detected but no PIN provided" | No PIN in env or parameter | Set `MESSENGER_E2EE_PIN` in `.env` |
| E2EE conversation not detected | URL doesn't contain `/e2ee/` | Check conversation URL in browser |
| PIN dialog keeps appearing | Session expired | Re-login via noVNC and re-warm |

## Source files

- `social-automation/backend/app/services/browser_bridge.py` — E2EE PIN handler and navigation
- `social-automation/backend/app/worker/tasks/personal_messenger.py` — Polling task E2EE integration

## Future enhancements

- Auto-detect PIN from Messenger settings (avoid manual env var)
- PIN caching across sessions (store in encrypted storage)
- E2EE message encryption/decryption at the API level (not just browser)
- Support for E2EE group conversations
- Fallback to non-E2EE if PIN entry fails repeatedly
