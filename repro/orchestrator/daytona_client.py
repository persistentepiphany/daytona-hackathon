"""Daytona SDK adapter: client construction plus environment plumbing.

The generated API clients build urllib3 pools that ignore the standard proxy
environment variables. On networks where outbound HTTPS must traverse a proxy
(HTTPS_PROXY set), that makes every SDK call bypass the proxy and fail, while
plain httpx/curl succeed. `enable_proxy_env()` patches the Configuration classes
of all sync client packages to honor HTTPS_PROXY and the CA bundle env vars; it
is a no-op when those variables are absent.
"""

import os


def _proxy_settings() -> tuple[str | None, str | None]:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return proxy, ca


_patched = False


def enable_proxy_env() -> None:
    global _patched
    if _patched:
        return
    proxy, ca = _proxy_settings()
    if not proxy:
        _patched = True
        return
    import daytona_analytics_api_client.configuration as an
    import daytona_api_client.configuration as api
    import daytona_toolbox_api_client.configuration as tb

    for mod in (api, tb, an):
        cls = mod.Configuration
        orig = cls.__init__

        def patched(self, *args, _orig=orig, **kwargs):
            _orig(self, *args, **kwargs)
            if not self.proxy:
                self.proxy = proxy
                if ca:
                    self.ssl_ca_cert = ca

        cls.__init__ = patched
    _patched = True


def make_daytona():
    """Construct a Daytona client from DAYTONA_API_KEY (or DAYTONA_API), proxy-aware."""
    enable_proxy_env()
    from daytona import Daytona, DaytonaConfig

    key = os.environ.get("DAYTONA_API_KEY") or os.environ.get("DAYTONA_API")
    if not key:
        raise RuntimeError("no DAYTONA_API_KEY / DAYTONA_API in environment")
    return Daytona(DaytonaConfig(api_key=key))
