import re

BASE = "/srv/dev-disk-by-uuid-fa6231ab-eae7-40ea-a4b6-400f767a89d7/talk-sip-bridge/src/bridge"

# --- dialin_ivr.py: fallback_token param + silence -> default room ---
p = f"{BASE}/dialin_ivr.py"
s = open(p).read()

old = """    def __init__(self, rtp, resolve=None, *, prompt_wav="", pin_prompt_wav="",
                 attempts: int = ATTEMPTS, first_digit_timeout: float = None,
                 next_digit_timeout: float = None, prompt_gap: float = None):
        self.rtp = rtp
        self.resolve = resolve or talk_sip_bridge.join_by_meeting_id"""
new = """    def __init__(self, rtp, resolve=None, *, prompt_wav="", pin_prompt_wav="",
                 attempts: int = ATTEMPTS, first_digit_timeout: float = None,
                 next_digit_timeout: float = None, prompt_gap: float = None,
                 fallback_token: str = ""):
        self.rtp = rtp
        self.resolve = resolve or talk_sip_bridge.join_by_meeting_id
        self.fallback_token = fallback_token"""
assert old in s, "init block not found"
s = s.replace(old, new, 1)

old2 = """            if not token:
                print("[ivr] Nothing was keyed in")
                self.play(self.rejected)
                continue"""
new2 = """            if not token:
                if self.fallback_token:
                    # A caller who keys nothing still meant to reach
                    # somebody: put them in the line's default room
                    # instead of ending the call on them.
                    outcome, room = self.resolve(self.fallback_token)
                    if outcome == talk_sip_bridge.OK:
                        print(f"[ivr] Nothing keyed in - defaulting into {room['token']}")
                        self.play(self.accepted)
                        return room
                    print(f"[ivr] Default room {self.fallback_token} refused the caller")
                print("[ivr] Nothing was keyed in")
                self.play(self.rejected)
                continue"""
assert old2 in s, "silence branch not found"
s = s.replace(old2, new2, 1)
open(p, "w").write(s)

# --- inbound_call.py: pass the line's default room into the IVR ---
p2 = f"{BASE}/inbound_call.py"
s2 = open(p2).read()
old3 = """        ivr = DialInIvr(rtp, prompt_wav=config.ivr_prompt_wav,
                        pin_prompt_wav=config.ivr_pin_prompt_wav)"""
new3 = """        ivr = DialInIvr(rtp, prompt_wav=config.ivr_prompt_wav,
                        pin_prompt_wav=config.ivr_pin_prompt_wav,
                        fallback_token=self.client.call_manager.line.default_room_token)"""
assert old3 in s2, "ivr construction not found"
s2 = s2.replace(old3, new3, 1)
open(p2, "w").write(s2)

import ast
for f in (f"{BASE}/dialin_ivr.py", f"{BASE}/inbound_call.py"):
    ast.parse(open(f).read())
print("patched + syntax-ok")
