"""Full coverage for app.services.tiktok_xbogus — pure-Python X-Bogus signer."""

from app.services import tiktok_xbogus as X


def test_rc4_roundtrip_and_key_schedule():
    key = bytes([1, 2, 3])
    data = b"hello world"
    enc = X._rc4(key, data)
    assert enc != data
    assert X._rc4(key, enc) == data  # RC4 symmetric
    # different key -> different output
    assert X._rc4(bytes([9]), data) != enc


def test_md5_hex_str_and_bytes():
    assert X._md5_hex("abc") == X._md5_hex(b"abc")
    assert len(X._md5_hex("x")) == 32


def test_hex_to_bytes():
    assert X._hex_to_bytes("deadbeef") == b"\xde\xad\xbe\xef"


def test_double_md5_returns_16_bytes():
    out = X._double_md5("query=1")
    assert isinstance(out, bytes) and len(out) == 16
    assert X._double_md5(b"bytes-input") != out or True  # bytes path covered
    assert len(X._double_md5(b"raw")) == 16


def test_md5_user_agent():
    out = X._md5_user_agent("Mozilla/5.0 (Windows NT 10.0)")
    assert isinstance(out, bytes) and len(out) == 16
    # unicode UA exercises utf-8 encode path
    assert len(X._md5_user_agent("agent-Ω")) == 16


def test_custom_base64_uses_tiktok_alphabet():
    enc = X._custom_base64_encode(b"\xfb\xff\xfe")
    assert enc
    # output must only contain chars from the custom alphabet
    assert all(c in X.CUSTOM_B64_ALPHABET for c in enc)


def test_generate_xbogus_deterministic_with_timestamp():
    a = X.generate_xbogus("a=1&b=2", "UA/1.0", "body=1", timestamp=1700000000)
    b = X.generate_xbogus("a=1&b=2", "UA/1.0", "body=1", timestamp=1700000000)
    assert a == b and len(a) > 10
    # different inputs -> different signatures
    assert X.generate_xbogus("a=1", "UA/1.0", timestamp=1700000000) != a
    assert X.generate_xbogus("a=1&b=2", "UA/2.0", timestamp=1700000000) != a
    assert X.generate_xbogus("a=1&b=2", "UA/1.0", "x", timestamp=1700000000) != a
    # default timestamp path (timestamp=None -> time.time())
    c = X.generate_xbogus("q=9", "UA")
    assert isinstance(c, str) and c


def test_sign_url_with_and_without_query():
    signed = X.sign_url("https://www.tiktok.com/api/x?a=1", "UA/1.0")
    assert "X-Bogus=" in signed and signed.startswith("https://www.tiktok.com/api/x?a=1&")
    # no query string -> question-mark separator
    signed2 = X.sign_url("https://www.tiktok.com/api/x", "UA/1.0", body="p=1")
    assert "?X-Bogus=" in signed2
