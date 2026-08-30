"""Daytona SDK adapter: client construction plus environment plumbing.

The generated API clients build urllib3 pools that ignore the standard proxy
environment variables. On networks where outbound HTTPS must traverse a proxy
(HTTPS_PROXY set), that makes every SDK call bypass the proxy and fail, while
plain httpx/curl succeed. `enable_proxy_env()` patches the Configuration classes
of all sync client packages to honor HTTPS_PROXY and the CA bundle env vars; it
is a no-op when those variables are absent.
"""

import time

from ..env import env_key


def _proxy_settings() -> tuple[str | None, str | None]:
    proxy = env_key("HTTPS_PROXY", "https_proxy")
    ca = env_key("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
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

    key = env_key("DAYTONA_API_KEY", "DAYTONA_API")
    if not key:
        raise RuntimeError("no DAYTONA_API_KEY / DAYTONA_API in environment or .env")
    return Daytona(DaytonaConfig(api_key=key))


class DaytonaAdapter:
    """Real SandboxAdapter over the Daytona SDK. See adapter.SandboxAdapter for the contract."""

    def __init__(self, daytona=None):
        self.d = daytona or make_daytona()
        self._boxes: dict[str, object] = {}

    def _get(self, sandbox_id: str):
        box = self._boxes.get(sandbox_id)
        if box is None:
            box = self.d.get(sandbox_id)
            self._boxes[sandbox_id] = box
        return box

    def create(self, spec) -> str:
        import time as _time

        from daytona import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            GpuType,
            Image,
            Resources,
            VolumeMount,
        )

        common = dict(
            name=spec.name,
            labels=spec.labels,
            ttl_minutes=spec.ttl_minutes,
            auto_stop_interval=spec.auto_stop_interval,
            auto_pause_interval=spec.auto_pause_interval,
            auto_delete_interval=spec.auto_delete_interval,
            volumes=[VolumeMount(volume_id=vid, mount_path=mp) for vid, mp in spec.volumes] or None,
            network_block_all=spec.network_block_all,
            env_vars=spec.env_vars or None,
        )
        common = {k: v for k, v in common.items() if v is not None}
        if spec.network_block_all:
            common["network_block_all"] = True
        if spec.snapshot:
            params = CreateSandboxFromSnapshotParams(snapshot=spec.snapshot, **common)
        elif spec.image:
            res = None
            if spec.resources:
                r = dict(spec.resources)
                gpu_type = r.pop("gpu_type", None)
                if isinstance(gpu_type, str):
                    gpu_type = GpuType(gpu_type)
                res = Resources(**r, gpu_type=gpu_type)
            params = CreateSandboxFromImageParams(image=Image.base(spec.image), resources=res, **common)
        else:
            raise ValueError("spec needs snapshot or image")
        t0 = _time.monotonic()
        box = self.d.create(params, timeout=420)
        self._boxes[box.id] = box
        self.last_create_seconds = _time.monotonic() - t0
        return box.id

    def fork(self, sandbox_id: str, name: str) -> str:
        child = self._get(sandbox_id).fork(name=name, timeout=300)
        self._boxes[child.id] = child
        return child.id

    def exec(self, sandbox_id: str, cmd: str, cwd: str | None = None,
             env: dict[str, str] | None = None, timeout: int | None = None):
        from .adapter import ExecResult

        r = self._get(sandbox_id).process.exec(cmd, cwd=cwd, env=env, timeout=timeout)
        return ExecResult(exit_code=r.exit_code, output=r.result or "")

    def create_snapshot(self, sandbox_id: str, name: str) -> None:
        self._get(sandbox_id).create_snapshot(name, timeout=1800)

    def stop(self, sandbox_id: str) -> None:
        self.d.stop(self._get(sandbox_id), timeout=120)

    def start(self, sandbox_id: str) -> None:
        self.d.start(self._get(sandbox_id), timeout=300)

    def delete(self, sandbox_id: str) -> None:
        self.d.delete(self._get(sandbox_id), timeout=120, wait=True)
        self._boxes.pop(sandbox_id, None)

    def list_ids_by_label(self, key: str, value: str) -> list[str]:
        from daytona import ListSandboxesQuery

        return [s.id for s in self.d.list(ListSandboxesQuery(labels={key: value}))]

    def read_file(self, sandbox_id: str, path: str) -> bytes:
        data = self._get(sandbox_id).fs.download_file(path)
        if data is None:
            raise FileNotFoundError(f"{sandbox_id}:{path}")
        return data

    def write_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        self._get(sandbox_id).fs.upload_file(data, path)

    def volume_ensure(self, name: str) -> str:
        import time as _time

        vol = self.d.volume.get(name, create=True)
        t0 = _time.monotonic()
        while getattr(vol, "state", None) != "ready":
            if _time.monotonic() - t0 > 180:
                raise TimeoutError(f"volume {name} stuck in {getattr(vol, 'state', '?')}")
            _time.sleep(2)
            vol = self.d.volume.get(name)
        return vol.id

    def volume_delete(self, name: str) -> None:
        self.d.volume.delete(self.d.volume.get(name))

    def snapshot_exists(self, name: str) -> bool:
        try:
            self.d.snapshot.get(name)
            return True
        except Exception:
            return False

    def snapshot_delete(self, name: str) -> None:
        self.d.snapshot.delete(self.d.snapshot.get(name))

    # --- async session commands (live feed log tap) ------------------------
    # Session commands take no cwd/env arguments, so both are folded into the command
    # string. These are tap commands only - never an experiment - so nothing hashed
    # into evidence depends on their shape.
    def exec_async(self, sandbox_id: str, cmd: str, cwd: str | None = None,
                   env: dict[str, str] | None = None):
        import shlex
        import uuid as _uuid

        from daytona import SessionExecuteRequest

        from .adapter import AsyncCmd

        parts = []
        for key, value in (env or {}).items():
            parts.append(f"export {key}={shlex.quote(str(value))};")
        if cwd:
            parts.append(f"cd {shlex.quote(cwd)} &&")
        parts.append(cmd)
        session_id = f"repro-tap-{_uuid.uuid4().hex[:8]}"
        proc = self._get(sandbox_id).process
        proc.create_session(session_id)
        resp = proc.execute_session_command(
            session_id, SessionExecuteRequest(command=" ".join(parts), run_async=True))
        return AsyncCmd(sandbox_id=sandbox_id, session_id=session_id,
                        cmd_id=resp.cmd_id)

    def follow_logs(self, handle, on_stdout, on_stderr=None) -> None:
        """Block, feeding output to the callbacks as it arrives.

        The SDK's streaming form is a coroutine over a websocket, so it runs on a
        private event loop in the caller's thread. Where that websocket cannot be
        established - an outbound proxy that refuses upgrades, most commonly - this
        degrades to polling the buffered log and diffing by byte offset. Both paths
        drive the same callbacks, so nothing above this method knows which ran.
        """
        on_stderr = on_stderr or on_stdout
        proc = self._get(handle.sandbox_id).process
        try:
            import asyncio

            box = self.d.get(handle.sandbox_id)
            asyncio.run(box.process.get_session_command_logs_async(
                handle.session_id, handle.cmd_id, on_stdout, on_stderr))
            return
        except Exception:
            pass  # fall through to polling; a tap must never fail a run
        self._poll_logs(proc, handle, on_stdout)

    def _poll_logs(self, proc, handle, on_stdout, interval: float = 0.5) -> None:
        seen = 0
        while True:
            try:
                logs = proc.get_session_command_logs(handle.session_id, handle.cmd_id)
                text = (getattr(logs, "output", None) or "")
                if len(text) > seen:
                    on_stdout(text[seen:])
                    seen = len(text)
                cmd = proc.get_session_command(handle.session_id, handle.cmd_id)
                if getattr(cmd, "exit_code", None) is not None:
                    return
            except Exception:
                return
            time.sleep(interval)

    def cancel_async(self, handle) -> None:
        """Always paired with exec_async: an undeleted session leaves an orphaned
        follower process burning sandbox CPU for the rest of the run."""
        try:
            self._get(handle.sandbox_id).process.delete_session(handle.session_id)
        except Exception:
            pass

    def preview_url(self, sandbox_id: str, port: int) -> str:
        return self._get(sandbox_id).get_preview_link(port).url

    def signed_preview_url(self, sandbox_id: str, port: int, expires_in_seconds: int = 86400) -> str:
        return self._get(sandbox_id).create_signed_preview_url(port, expires_in_seconds).url
