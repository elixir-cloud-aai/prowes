# Access control

## Authentication

The WES API requires an access token of a trusted identity provider (OpenID
Connect), sent as a bearer token, for all operations on runs:

```bash
curl -X GET "http://localhost:8090/ga4gh/wes/v1/runs" \
  -H "Authorization: Bearer <token>" -H "Accept: application/json"
```

`GET /service-info`, the specification (`/ga4gh/wes/v1/openapi.json`) and
the Swagger UI (`/ga4gh/wes/v1/ui/`) are public. Requests without a valid
token are rejected with `401 Unauthorized` and a Bearer challenge
(`WWW-Authenticate: Bearer`, RFC 6750; with `error="invalid_token"` for
invalid tokens), as [problem details](api.md#errors).

proWES accepts only tokens that:

- are access tokens as per [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068)
  (`typ` header `at+jwt`), not, e.g., ID tokens;
- are issued by one of the trusted issuers (`security.auth.issuers`, the
  `iss` claim), and verified with the issuer's keys (from its OpenID Connect
  discovery document);
- are for one of the configured audiences (`security.auth.audience`, the
  `aud` claim), i.e., were issued to one of the apps that call proWES (e.g.,
  its front ends, by their client IDs);
- if `security.auth.clients` is set, were obtained by one of these clients
  (the `client_id` claim, or `azp` if there is none);
- have not expired.

The base config trusts no issuer, so all tokens are rejected (and a warning
is logged on startup) until a deployment sets its issuers and audiences,
e.g., in a config overlay:

```yaml
security:
  auth:
    issuers:
      - https://login.example.org/oidc/
    audience:
      - my-portal-client-id
```

If your identity provider sets another `typ` header on its access tokens,
list it in `security.auth.token_types`; if it sets none, disable the check
with `security.auth.verify_token_type: false`. See [FOCA's authentication
options](https://foca.readthedocs.io/en/latest/configuration/auth/) for all
settings (e.g., several issuers, claims from the user info endpoint).

Tokens of clients are never passed on to the WES instances that runs are
forwarded to. Credentials of proWES for WES instances follow with
[#182](https://github.com/elixir-cloud-aai/prowes/issues/182); until then,
WES instances must accept requests without credentials (`auth: none`, see
[WES instances](configuration.md#wes-instances)).

Authentication can be switched off only by changing `disable_auth` of
`api.specs` in the configuration: then anyone who can reach proWES can
start runs, and runs have no owner (every client sees all runs). Don't do
this outside local tests.

## Run ownership

Each run belongs to the caller that created it: the subject of its token
(the claim `security.auth.claim_identity`, by default `sub`), namespaced by
the token's issuer as `<namespace>|<subject>`, so that equal subjects of
different issuers are different owners. The namespace is the issuer itself
(e.g., `https://login.example.org/oidc/|alice`), also if only one issuer is
trusted, so that owners stay the same when another issuer is trusted later
(and when proWES moves to FOCA 0.23, which namespaces subjects by default
the same way). Set `namespace` of an issuer (entries of `security.auth.issuers` as
`{issuer: <url>, namespace: <name>}`) for a shorter one, or an empty one to
keep its subjects bare; changing it later orphans the runs stored so far.
Owners are stored with each run (`user_id`) and taken from the token only,
never from the request.

Callers list their own runs only (`GET /runs`), and only they read, query
the status of and cancel a run (`GET /runs/{run_id}`, `GET
/runs/{run_id}/status`, `POST /runs/{run_id}/cancel`); requests for runs of
others get `403 Forbidden`. Tokens without a subject claim are invalid
(`401`); tokens whose subject can't own runs (an empty one, or a bare one
containing `|`) get `403 Forbidden`. Runs stored without
owner (e.g., before authentication was switched on) can't be read by
anyone. Roles, admins and sharing runs follow with access control
([#48](https://github.com/elixir-cloud-aai/prowes/issues/48)).
