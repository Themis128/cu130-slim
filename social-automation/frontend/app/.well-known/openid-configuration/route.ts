export function GET() {
  const body=JSON.stringify({"issuer": "https://social.cloudless.gr", "authorization_endpoint": "https://social.cloudless.gr/login", "token_endpoint": "https://social.cloudless.gr/api/v1/auth/login", "jwks_uri": "https://social.cloudless.gr/api/v1/auth/jwks.json", "grant_types_supported": ["password", "authorization_code"], "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"], "response_types_supported": ["token", "code"], "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["HS256"], "agent_auth": {"register_uri": "https://social.cloudless.gr/signup", "identity_types_supported": ["oauth2_client", "bot_token"], "credential_types_supported": ["bearer"], "revocation_uri": "https://social.cloudless.gr/api/v1/auth/logout"}});
  const ctype="application/json";
  return new Response(body,{headers:{"content-type":ctype,"access-control-allow-origin":"*","cache-control":"public, max-age=3600"}});
}
