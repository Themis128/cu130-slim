/**
 * GET /.well-known/http-message-signatures-directory — Web Bot Auth JWKS.
 * Publishes the Ed25519 public key SocialAuto agents use to sign outbound
 * requests (HTTP Message Signatures, RFC 9421). Receiving sites verify
 * Signature/Signature-Input headers against this key.
 */
const JWKS = {
  keys: [
    {
      kty: "OKP",
      crv: "Ed25519",
      x: "tYRI8xtCCA4JhHI8eHhFbmHu0ol1rcORbQmZ1iPHr8o",
      alg: "EdDSA",
      use: "sig",
      kid: "Bng5NAERjK8vX06O2B1dwWmDMGxhJ9WaL34mCnNYXE8",
    },
  ],
};

export function GET() {
  return new Response(JSON.stringify(JWKS), {
    headers: { "content-type": "application/json", "cache-control": "public, max-age=86400" },
  });
}
