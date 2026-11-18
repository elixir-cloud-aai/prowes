# Access control

!!! warning "No authentication yet"
    proWES currently serves the WES API **without authentication**
    (`disable_auth: True` in `api.specs` of
    [`pro_wes/config.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/config.yaml)):
    anyone who can reach it can start, list, read and cancel runs. Don't
    expose it to untrusted networks. Authentication of clients and of WES
    instances follows with
    [#120](https://github.com/elixir-cloud-aai/prowes/issues/120), as in
    [proTES](https://protes.readthedocs.io/en/latest/access-control/).

## Authentication

Authentication is configured in the section `security.auth` of the
configuration (see [FOCA's auth
reference](https://foca.readthedocs.io/en/latest/configuration/auth/)).
Without trusted `issuers`, which is the default, FOCA rejects all bearer
tokens, so the API can't be protected by setting `disable_auth: False` alone.

## Run ownership

Runs are stored with the identity of the client that started them, if the
request has one; clients then list and read only their own runs. Without
authentication, runs have no owner and every client sees all runs.
