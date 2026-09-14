"""TikTok captcha solver via mobile API with signed requests.

Uses the free tiktok-signer library (pip install tiktok-signer) for
X-Argus/X-Gorgon/X-Ladon signatures, and the edata ChaCha encryption
for request/response payloads.

Based on the Gisnsl/tiktok-captcha-solver approach but using the
free tiktok-signer instead of the proprietary TikSign library.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import secrets
import time
from urllib.parse import urlencode

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ─── ChaCha encryption for edata ──────────────────────────────────────

class _Cha:
    def __init__(self, key: bytes, nonce: bytes, counter: int = 0):
        self.k = key
        self.n = nonce
        self.c = counter

    @staticmethod
    def _r(v, n):
        return ((v << n) & 0xFFFFFFFF) | (v >> (32 - n))

    @staticmethod
    def _qr(s, a, b, c, d):
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF
        s[d] ^= s[a]
        s[d] = _Cha._r(s[d], 16)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF
        s[b] ^= s[c]
        s[b] = _Cha._r(s[b], 12)
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF
        s[d] ^= s[a]
        s[d] = _Cha._r(s[d], 8)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF
        s[b] ^= s[c]
        s[b] = _Cha._r(s[b], 7)

    def _block(self, ctr):
        s = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]
        s += [int.from_bytes(self.k[i*4:(i+1)*4], "little") for i in range(8)]
        s.append(ctr & 0xFFFFFFFF)
        s += [int.from_bytes(self.n[i*4:(i+1)*4], "little") for i in range(3)]
        w = s[:]
        for _ in range(10):
            self._qr(w, 0, 4, 8, 12)
            self._qr(w, 1, 5, 9, 13)
            self._qr(w, 2, 6, 10, 14)
            self._qr(w, 3, 7, 11, 15)
            self._qr(w, 0, 5, 10, 15)
            self._qr(w, 1, 6, 11, 12)
            self._qr(w, 2, 7, 8, 13)
            self._qr(w, 3, 4, 9, 14)
        return b''.join(((w[i]+s[i]) & 0xFFFFFFFF).to_bytes(4, "little") for i in range(16))

    def _ks(self):
        ctr = self.c
        while True:
            block = self._block(ctr)
            ctr = (ctr + 1) & 0xFFFFFFFF
            yield from block

    def _p(self, data):
        ks = self._ks()
        return bytes([b ^ next(ks) for b in data])


def decrypt_edata(txt: str) -> str:
    padding = 4 - len(txt) % 4
    if padding != 4:
        txt += "=" * padding
    raw = base64.b64decode(txt)
    k = raw[1:33]
    n = raw[33:45]
    ct = raw[45:]
    return _Cha(k, n, 0)._p(ct).decode("utf-8", errors="replace")


def encrypt_edata(txt: str) -> str:
    d = txt.encode("utf-8")
    k = secrets.token_bytes(32)
    n = secrets.token_bytes(12)
    ct = _Cha(k, n, 0)._p(d)
    return base64.b64encode(b"\x01" + k + n + ct).decode()


# ─── Image processing ─────────────────────────────────────────────────

def _decode_img(b64: str) -> np.ndarray:
    data = base64.b64decode(b64)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Failed to decode image")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    return img


def _sobel(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    ax = cv2.convertScaleAbs(gx)
    ay = cv2.convertScaleAbs(gy)
    grad = cv2.addWeighted(ax, 0.5, ay, 0.5, 0)
    return cv2.normalize(grad, None, 0, 255, cv2.NORM_MINMAX)


def _enhance(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)


def _edges(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(blurred, 50, 150)


def find_gap_position(bg_b64: str, piece_b64: str) -> int:
    """Find the gap position using the PuzzleSolver algorithm."""
    bg = _decode_img(bg_b64)
    piece = _decode_img(piece_b64)

    logger.info("TikTok captcha: bg shape=%s, piece shape=%s", bg.shape, piece.shape)

    methods = (cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR_NORMED)
    results = []

    p_sobel = _sobel(piece)
    t_sobel = _sobel(bg)
    for m in methods:
        matched = cv2.matchTemplate(t_sobel, p_sobel, m)
        mn, mx, mn_loc, mx_loc = cv2.minMaxLoc(matched)
        results.append((mx_loc[0], mx))

    p_enh = _enhance(p_sobel)
    t_enh = _enhance(t_sobel)
    for m in methods:
        matched = cv2.matchTemplate(t_enh, p_enh, m)
        mn, mx, mn_loc, mx_loc = cv2.minMaxLoc(matched)
        results.append((mx_loc[0], mx))

    p_edges = _edges(piece)
    t_edges = _edges(bg)
    matched = cv2.matchTemplate(t_edges, p_edges, cv2.TM_CCOEFF_NORMED)
    mn, mx, mn_loc, mx_loc = cv2.minMaxLoc(matched)
    results.append((mx_loc[0], mx))

    results.sort(key=lambda x: x[1], reverse=True)
    best_pos = results[0][0]

    logger.info(
        "TikTok captcha gap position: %d (confidence: %.3f, all: %s)",
        best_pos, results[0][1], [(p, round(c, 3)) for p, c in results[:5]],
    )
    return best_pos


# ─── Mobile API captcha solver ────────────────────────────────────────

def _get_params(iid: str = "", dev: str = "") -> dict:
    """Build captcha API query parameters."""
    return {
        "lang": "en",
        "app_name": "musical_ly",
        "h5_sdk_version": "2.33.17",
        "h5_sdk_use_type": "goofy",
        "sdk_version": "2.3.8.i18n",
        "iid": iid,
        "did": dev,
        "device_id": dev,
        "ch": "googleplay",
        "aid": "1233",
        "os_type": "0",
        "mode": "",
        "tmp": str(int(time.time() * 1000)),
        "platform": "app",
        "webdriver": "false",
        "enable_image": "1",
        "verify_host": "https://rc-verification-sg.tiktokv.com/",
        "locale": "en",
        "channel": "googleplay",
        "app_key": "",
        "vc": "37.0.4",
        "app_version": "37.0.4",
        "session_id": "",
        "region": "sg",
        "userMode": "257",
        "use_native_report": "1",
        "use_jsb_request": "1",
        "orientation": "2",
        "resolution": "1080*2220",
        "os_version": "30",
        "device_brand": "Redmi",
        "device_model": "Redmi Note 8 Pro",
        "os_name": "Android",
        "version_code": "3704",
        "device_type": "Redmi Note 8 Pro",
        "device_platform": "Android",
        "type": "verify",
        "detail": "",
        "server_sdk_env": '{"idc":"my","region":"ALISG","server_type":"business"}',
        "imagex_domain": "",
        "subtype": "slide",
        "challenge_code": "99999",
        "triggered_region": "sg",
        "cookie_enabled": "true",
        "screen_width": "393",
        "screen_height": "851",
        "browser_language": "en",
        "browser_platform": "Linux aarch64",
        "browser_name": "Mozilla",
        "browser_version": "5.0 (Linux; Android 11; Redmi Note 8 Pro Build/RP1A.200720.011; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.34 Mobile Safari/537.36 BytedanceWebview/d8a21c6",  # noqa: E501
    }


def solve_captcha_via_mobile_api() -> dict | None:
    """Solve a TikTok captcha via the mobile API.

    Returns the verify response dict if successful, None otherwise.
    """
    import requests
    from tiktok_signer import TikTokSigner

    session = requests.Session()
    params = _get_params()
    params_str = urlencode(params)

    # Step 1: Get the captcha challenge
    logger.info("TikTok captcha: fetching challenge from mobile API...")
    signs = TikTokSigner.generate_headers(params=params_str)
    headers = {
        "content-type": "application/json; charset=utf-8",
        "user-agent": "com.zhiliaoapp.musically/370004 (Linux; U; Android 11; en; Redmi Note 8 Pro; Build/RP1A.200720.011; Cronet/143.0.7499.34)",
    }
    headers.update(signs)

    resp = session.get(
        "https://rc-verification-sg.tiktokv.com/captcha/get",
        headers=headers,
        params=params,
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("TikTok captcha: get failed with status %d", resp.status_code)
        return None

    data = resp.json()
    edata_str = data.get("edata") or (data.get("data", {}) or {}).get("edata")
    if not edata_str:
        logger.error("TikTok captcha: no edata in response")
        return None

    decrypted = decrypt_edata(edata_str)
    captcha_data = json.loads(decrypted)
    logger.info("TikTok captcha: got challenge, mode=%s", captcha_data.get("data", {}).get("challenges", [{}])[0].get("mode"))

    # Step 2: Extract the challenge
    challenges = captcha_data.get("data", {}).get("challenges", [])
    slide_challenge = None
    for ch in challenges:
        if ch.get("mode") in ("whirl", "slide"):
            slide_challenge = ch
            break

    if not slide_challenge:
        logger.error("TikTok captcha: no slide challenge found")
        return None

    question = slide_challenge.get("question", {})
    url1 = question.get("url1")
    url2 = question.get("url2")
    if not url1 or not url2:
        logger.error("TikTok captcha: missing image URLs")
        return None

    # Step 3: Download images
    logger.info("TikTok captcha: downloading images...")
    r1 = session.get(url1, timeout=10)
    r2 = session.get(url2, timeout=10)
    if r1.status_code != 200 or r2.status_code != 200:
        logger.error("TikTok captcha: image download failed (%d, %d)", r1.status_code, r2.status_code)
        return None

    bg_b64 = base64.b64encode(r1.content).decode()
    piece_b64 = base64.b64encode(r2.content).decode()

    # Step 4: Find the gap position
    gap_x = find_gap_position(bg_b64, piece_b64)

    # Step 5: Generate human-like movement data
    rand_len = random.randint(40, 100)
    movements = []
    total_time = 0
    tip_y = question.get("tip_y", 0)
    for i in range(rand_len):
        progress = (i + 1) / rand_len
        x_pos = round(gap_x * progress)
        y_offset = random.randint(-2, 2) if 0 < i < rand_len - 1 else 0
        y_pos = tip_y + y_offset
        step = random.randint(8, 40)
        total_time += step
        movements.append({"relative_time": total_time, "x": x_pos, "y": y_pos})

    # Step 6: Verify the captcha
    payload = {
        "modified_img_width": 552,
        "id": slide_challenge["id"],
        "mode": "slide",
        "reply": movements,
        "verify_id": captcha_data["data"]["verify_id"],
    }

    json_data = json.dumps({"edata": encrypt_edata(json.dumps(payload))})
    logger.info("TikTok captcha: verifying...")
    signs = TikTokSigner.generate_headers(params=params_str, data=json_data)
    headers.update(signs)

    resp = session.post(
        "https://rc-verification-sg.tiktokv.com/captcha/verify",
        headers=headers,
        params=params,
        data=json_data,
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("TikTok captcha: verify failed with status %d", resp.status_code)
        return None

    verify_data = resp.json()
    verify_edata = verify_data.get("edata") or (verify_data.get("data", {}) or {}).get("edata")
    if verify_edata:
        verify_result = decrypt_edata(verify_edata)
        logger.info("TikTok captcha: verify result: %s", verify_result)
        return json.loads(verify_result)

    logger.info("TikTok captcha: verify response: %s", json.dumps(verify_data))
    return verify_data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = solve_captcha_via_mobile_api()
    if result:
        print("\n=== RESULT ===")
        print(json.dumps(result, indent=2))
    else:
        print("Failed to solve captcha")
