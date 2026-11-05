#!/usr/bin/env python3
"""Trim consent-submit-rejected scopes from a staged LinkedIn consent URL.

Some scopes pass the authorize-URL probe but LinkedIn rejects them at consent
submit with ?error=unauthorized_scope_error&error_description=Scope+"X"+...
Run this after each failed click to strip the named scope and retry — until
the URL lands on the callback with a code (or a login wall).

Usage: li_consent_trim.py <url-file> <scope
