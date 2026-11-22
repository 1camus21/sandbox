"""Domain-independent episode lifecycle and execution evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from time import monotonic_ns
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from sandbox.storage import write_atomic


SCHEMA_VERSION = 3
HTTP_FAULTS = {
    "TIMEOUT_COMMITTED": 504,
    "TIMEOUT_NOT_COMMITTED": 504,
    "CONNECTION_FAIL": 502,
    "AUTH_401": 401,
}
log = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    tool: str = "*"
    nth: int = Field(default=1, ge=1)
    at: Literal["start", "call"] = "call"
    target: str | None = None
    persist: bool = False
    mode: Literal["http", "rpc-error"] = "http"
    delaySeconds: float = Field(default=0, ge=0, allow_inf_nan=False)


@dataclass(frozen=True)
class Outcome:
    success: bool | None
    result: dict | str
    text: str


class Domain(Protocol):
    name: str
    tools: list[dict]
    business_faults: frozenset[str]

    def worlds(self) -> list[str]: ...
    def accounts(self) -> list[str]: ...
    def load_world(self, world_id: str, seed: int) -> dict: ...
    def load_account(self, account_id: str | None, world: dict, seed: int) -> dict: ...
    def world_window(self, world: dict) -> dict | None: ...
    def world_state(self, world: dict) -> dict: ...
    def account_state(self, account: dict) -> dict: ...
    def state(self, world: dict, account: dict) -> dict[str, dict]: ...
    def fault(self, world: dict, account: dict, fault: Fault) -> None: ...
    def call(self, episode: Episode, tool: str, arguments: dict) -> Outcome: ...


@dataclass(frozen=True)
class DomainConfig:
    """一局里一个领域的开局参数；inline 数据存在时优先于标识引用。"""

    domain: Domain
    scene: str
    account: str | None = None
    world_inline: dict | None = None
    account_inline: dict | None = None


class EpisodeConflict(RuntimeError):
    pass


def _parse_moment(value: str) -> datetime:
    """按业务本地时间比较；窗口与场景时钟都不带时区参与运算。"""
    return datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=None)


def _resolve_clock(worlds: dict[str, dict], domains: dict[str, Domain], primary: str,
                   requested: str | None) -> tuple[datetime, dict | None]:
    """按世界窗口确定一局的时钟；窗口无交集时拒绝开局。"""
    windows = []
    for name, world in worlds.items():
        window = domains[name].world_window(world)
        if window is not None:
            windows.append((name, window, _parse_moment(window["from"]), _parse_moment(window["to"])))
    if requested is not None:
        moment = _parse_moment(requested)
        for name, window, start, end in windows:
            if not start <= moment <= end:
                raise ValueError(f"clock outside world window: {name} {window['from']}..{window['to']}")
        return moment, None
    if not windows:
        return _parse_moment(worlds[primary]["clock"]), None
    start = max(item[2] for item in windows)
    end = min(item[3] for item in windows)
    if start > end:
        detail = ", ".join(f"{name} {window['from']}..{window['to']}"
                           for name, window, _, _ in windows)
        raise ValueError(f"worlds have no overlapping window: {detail}")
    primary_clock = _parse_moment(worlds[primary]["clock"])
    if start <= primary_clock <= end:
        moment = primary_clock
    elif start + timedelta(hours=1) <= end:
        moment = start + timedelta(hours=1)
    else:
        moment = start
    return moment, {"from": start.isoformat(), "to": end.isoformat()}


class Episode:
    def __init__(self, configs: list[DomainConfig], seed: int, faults: list[Fault],
                 episode_id: str | None = None, clock: str | None = None):
        if not configs:
            raise ValueError("episode needs at least one domain")
        names = [config.domain.name for config in configs]
        if len(set(names)) != len(names):
            raise ValueError("repeated domain")
        self.episode_id = episode_id or uuid4().hex
        self.configs = tuple(configs)
        self.domains = {config.domain.name: config.domain for config in configs}
        self.scene_ids = {config.domain.name: ("@inline" if config.world_inline is not None else config.scene)
                          for config in configs}
        self.account_ids: dict[str, str] = {}
        self.worlds: dict[str, dict] = {}
        self.accounts: dict[str, dict] = {}
        self.seed = seed
        self.faults = tuple(faults)
        self.primary = names[0]
        self._current = self.primary
        tool_schema = json.dumps([tool for config in configs for tool in config.domain.tools],
                                 sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.tool_schema_sha256 = hashlib.sha256(tool_schema.encode()).hexdigest()
        self.lock = RLock()
        self.generation = 0
        self.closed = False
        self.closing = False
        self.in_flight = 0
        self.tool_domains: dict[str, str] = {}
        for config in configs:
            for tool in config.domain.tools:
                name = tool["name"]
                if name in self.tool_domains:
                    raise ValueError(f"tool {name} defined by multiple domains")
                self.tool_domains[name] = config.domain.name
        for config in configs:
            if config.world_inline is not None:
                loader = getattr(config.domain, "load_inline_world", None)
                if loader is None:
                    raise ValueError(f"domain {config.domain.name} does not support inline world")
                world = loader(config.world_inline, seed)
            else:
                world = config.domain.load_world(config.scene, seed)
            if config.account_inline is not None:
                loader = getattr(config.domain, "load_inline_account", None)
                if loader is None:
                    raise ValueError(f"domain {config.domain.name} does not support inline account")
                account = loader(config.account_inline, world, seed)
            else:
                account = config.domain.load_account(config.account, world, seed)
            self.worlds[config.domain.name] = world
            self.accounts[config.domain.name] = account
            self.account_ids[config.domain.name] = account.get("accountId") or (config.account or "")
        moment, window = _resolve_clock(self.worlds, self.domains, self.primary, clock)
        self.clock = moment.strftime("%Y-%m-%d %H:%M:%S")
        self.window = window
        for world in self.worlds.values():
            if "clock" in world:
                world["clock"] = self.clock
        self.start_faults: list[int] = []
        for index, fault in enumerate(self.faults):
            if fault.tool != "*" and fault.tool not in self.tool_domains:
                raise ValueError(f"unknown fault tool: {fault.tool}")
            owners = [name for name, domain in self.domains.items()
                      if fault.type in domain.business_faults]
            if fault.type in HTTP_FAULTS:
                if fault.at != "call" or fault.target is not None:
                    raise ValueError("transport faults apply to calls and take no target")
            elif owners:
                if fault.tool != "*" and self.tool_domains[fault.tool] not in owners:
                    raise ValueError(f"fault {fault.type} does not apply to {fault.tool}")
                if fault.mode != "http" or fault.delaySeconds:
                    raise ValueError("business faults take neither a transport mode nor a response delay")
                if fault.at == "start" and (fault.tool != "*" or fault.nth != 1 or fault.persist):
                    raise ValueError("start faults take no tool selector, ordinal, or persistence")
                for name in owners:
                    self.domains[name].fault(deepcopy(self.worlds[name]), deepcopy(self.accounts[name]), fault)
                    if fault.at == "start":
                        self.domains[name].fault(self.worlds[name], self.accounts[name], fault)
                if fault.at == "start":
                    self.start_faults.append(index)
            else:
                raise ValueError(f"unknown fault: {fault.type}")
        self.initial_worlds = deepcopy(self.worlds)
        self.initial_accounts = deepcopy(self.accounts)
        self.reset()

    @property
    def domain(self) -> Domain:
        return self.domains[self._current]

    @property
    def world(self) -> dict:
        return self.worlds[self._current]

    @property
    def account(self) -> dict:
        return self.accounts[self._current]

    @property
    def scene_id(self) -> str:
        return self.scene_ids[self._current]

    @property
    def account_id(self) -> str:
        return self.account_ids[self._current]

    def reset(self) -> None:
        with self.lock:
            self.ensure_idle()
            if self.closed:
                raise EpisodeConflict("episode is closed")
            self.generation += 1
            self.worlds = deepcopy(self.initial_worlds)
            self.accounts = deepcopy(self.initial_accounts)
            self._current = self.primary
            self.seq = self.round = 0
            self.calls: list[dict] = []
            self.counters: dict[str, int] = {}
            self.fired = set(self.start_faults)

    def ensure_idle(self) -> None:
        if self.in_flight or self.closing:
            raise EpisodeConflict("episode has requests in flight or is closing")

    def _due(self, tool: str, nth: int) -> list[int]:
        due = []
        for index, fault in enumerate(self.faults):
            if fault.at != "call" or fault.tool not in ("*", tool):
                continue
            count = self.seq + 1 if fault.tool == "*" else nth
            if (index not in self.fired and count == fault.nth) or (fault.persist and count >= fault.nth):
                due.append(index)
        return due

    def invoke(self, tool: str, arguments: dict, rpc_id: int | str, meta: dict | None,
               *, admitted_at: int | None = None) -> tuple[dict, Fault | None]:
        with self.lock:
            if self.closed:
                raise EpisodeConflict("episode is closed")
            name = self.tool_domains.get(tool, self.primary)
            domain = self.domains[name]
            self._current = name
            world, account = self.worlds[name], self.accounts[name]
            nth = self.counters.get(tool, 0) + 1
            due = self._due(tool, nth)
            transport = next((index for index in due if self.faults[index].type in HTTP_FAULTS), None)
            override = self.faults[transport] if transport is not None else None
            execute = override is None or override.type == "TIMEOUT_COMMITTED"
            business_faults = [index for index in due if self.faults[index].type in domain.business_faults]
            planned_status = (200 if override.mode == "rpc-error" else HTTP_FAULTS[override.type]) if override else None
            before = deepcopy(domain.state(world, account))
            event = {
                "schemaVersion": SCHEMA_VERSION,
                "type": "tool.call",
                "runId": self.episode_id,
                "generation": self.generation,
                "domain": name,
                "requestId": uuid4().hex,
                "requestedAt": utc_now(),
                "admittedMonotonicNs": admitted_at if admitted_at is not None else monotonic_ns(),
                "seq": self.seq + 1,
                "round": self.round,
                "nth": nth,
                "request": {"rpcId": rpc_id, "tool": tool, "arguments": deepcopy(arguments), "meta": deepcopy(meta)},
                "faults": [] if transport is None else [transport],
                "skippedFaults": [
                    {"index": index, "reason": "transport_fault_selected"}
                    for index in due if index != transport and self.faults[index].type in HTTP_FAULTS
                ] + [
                    {"index": index, "reason": "business_not_attempted"}
                    for index in business_faults if not execute
                ],
                "business": {"executed": False, "success": None, "result": None},
                "changes": [],
                "transport": {"state": "pending", "httpStatus": None, "body": None, "completedAt": None,
                              "plannedHttpStatus": planned_status},
            }
            if execute:
                for index in business_faults:
                    domain.fault(world, account, self.faults[index])
                event["faults"].extend(business_faults)
                try:
                    outcome = domain.call(self, tool, arguments)
                    event["business"] = {
                        "executed": True, "success": outcome.success, "result": deepcopy(outcome.result),
                        "text": outcome.text,
                    }
                except Exception:
                    # Keep the evidence if a domain fails after touching state.
                    log.exception("Domain %s failed calling %s", domain.name, tool)
                    event["business"] = {"executed": True, "success": None, "result": None, "error": "domain error"}

            after = domain.state(world, account)
            for collection in sorted(before.keys() | after.keys()):
                old_values, new_values = before.get(collection, {}), after.get(collection, {})
                for key in sorted(old_values.keys() | new_values.keys()):
                    old, new = old_values.get(key), new_values.get(key)
                    if old != new:
                        event["changes"].append({
                            "collection": collection, "id": key, "before": old, "after": deepcopy(new),
                        })
            self.fired.update(event["faults"])
            self.seq += 1
            self.counters[tool] = nth
            self.calls.append(event)
            return event, override

    def finish(self, event: dict, status: int | None, body: str | None, state: str) -> None:
        with self.lock:
            event["transport"].update(state=state, httpStatus=status, body=body, completedAt=utc_now(),
                                      completedMonotonicNs=monotonic_ns())

    def snapshot(self) -> dict:
        with self.lock:
            primary = self.primary
            domain = self.domains[primary]
            return deepcopy({
                "schemaVersion": SCHEMA_VERSION,
                "runId": self.episode_id,
                "episodeId": self.episode_id,
                "generation": self.generation,
                "domains": list(self.domains),
                "domain": primary,
                "toolSchemaSha256": self.tool_schema_sha256,
                "scene": self.scene_ids[primary],
                "worldId": self.scene_ids[primary],
                "worldIds": dict(self.scene_ids),
                "accountId": self.account_ids[primary],
                "accountIds": dict(self.account_ids),
                "clock": self.clock,
                "window": self.window,
                "seed": self.seed,
                "closed": self.closed,
                "round": self.round,
                "faultPlan": [fault.model_dump() for fault in self.faults],
                "startFaults": self.start_faults,
                "world": {name: self.domains[name].world_state(self.worlds[name]) for name in self.domains},
                "account": {
                    "initial": {name: self.domains[name].account_state(self.initial_accounts[name])
                                for name in self.domains},
                    "final": {name: self.domains[name].account_state(self.accounts[name])
                              for name in self.domains},
                },
                # 兼容单领域调用方：继续提供主领域的合并视图。
                "initialState": domain.state(self.initial_worlds[primary], self.initial_accounts[primary]),
                "state": domain.state(self.worlds[primary], self.accounts[primary]),
                "calls": self.calls,
            })

    def events(self) -> list[dict]:
        with self.lock:
            return deepcopy(self.calls)


class EpisodeStore:
    def __init__(self, archive_dir: Path | None = None):
        self.episodes: dict[str, Episode] = {}
        self.bindings: dict[str, Episode] = {}
        self.opened: dict[str, tuple] = {}
        self.used_credentials: set[str] = set()
        self.lock = RLock()
        self.archive_dir = archive_dir
        if archive_dir is not None:
            archive_dir.mkdir(parents=True, exist_ok=True)
            (archive_dir / ".credentials").mkdir(exist_ok=True)

    def create(self, token: str, configs: list[DomainConfig], seed: int, faults: list[Fault],
               episode_id: str | None = None, clock: str | None = None) -> tuple[str, bool]:
        credential = hashlib.sha256(token.encode()).hexdigest()
        config = (credential,
                  tuple((item.domain.name, item.scene, item.account,
                         json.dumps(item.world_inline, sort_keys=True),
                         json.dumps(item.account_inline, sort_keys=True)) for item in configs),
                  seed, tuple(faults), clock)
        with self.lock:
            if episode_id is not None and episode_id in self.opened:
                if self.opened[episode_id] != config:
                    raise EpisodeConflict("episode ID was already opened with different parameters")
                return episode_id, False
            claim = self.archive_dir / ".credentials" / credential if self.archive_dir is not None else None
            if credential in self.used_credentials or (claim is not None and claim.exists()):
                raise EpisodeConflict("MCP token already used by an episode")
            archive = self.archive_path(episode_id) if episode_id else None
            if archive is not None and archive.exists():
                raise EpisodeConflict("episode ID already has an archive")
            episode = Episode(configs, seed, faults, episode_id, clock)
            if claim is not None:
                try:
                    with claim.open("x", encoding="utf-8") as file:
                        file.write(episode.episode_id + "\n")
                except FileExistsError as error:
                    raise EpisodeConflict("MCP token already used by an episode") from error
            self.episodes[episode.episode_id] = self.bindings[token] = episode
            self.used_credentials.add(credential)
            self.opened[episode.episode_id] = config
            return episode.episode_id, True

    def get(self, episode_id: str) -> Episode | None:
        with self.lock:
            return self.episodes.get(episode_id)

    def bound(self, token: str) -> Episode | None:
        with self.lock:
            return self.bindings.get(token)

    def admit(self, token: str, scope: tuple[str, int] | None = None) -> Episode | None:
        with self.lock:
            episode = self.bindings.get(token)
            if episode is not None:
                with episode.lock:
                    if episode.closing or (scope or (episode.episode_id, 1)) != (episode.episode_id, episode.generation):
                        return None
                    episode.in_flight += 1
            return episode

    def list(self) -> list[dict]:
        with self.lock:
            rows = [{"episodeId": episode.episode_id, "closed": episode.closed, "domain": episode.primary,
                     "domains": list(episode.domains), "scene": episode.scene_ids[episode.primary]}
                    for episode in self.episodes.values()]
            if self.archive_dir is not None:
                rows.extend({"episodeId": path.stem, "closed": True, "archived": True}
                            for path in sorted(self.archive_dir.glob("*.json"))
                            if path.stem not in self.episodes and re.fullmatch(r"[0-9a-f]{32}", path.stem))
            return rows

    def archive_path(self, episode_id: str) -> Path | None:
        if self.archive_dir is not None and re.fullmatch(r"[0-9a-f]{32}", episode_id):
            return self.archive_dir / f"{episode_id}.json"
        return None

    def archived(self, episode_id: str) -> dict | None:
        path = self.archive_path(episode_id)
        if path is None:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def delete_archive(self, episode_id: str) -> bool:
        path = self.archive_path(episode_id)
        if path is None:
            return False
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def delete(self, episode: Episode) -> None:
        with self.lock:
            if not episode.closed:
                raise EpisodeConflict("close the episode before deleting it")
            self.episodes.pop(episode.episode_id, None)

    def close(self, episode: Episode, generation: int | None = None) -> dict:
        with self.lock, episode.lock:
            if generation is not None and episode.generation != generation:
                raise EpisodeConflict("episode generation changed")
            episode.ensure_idle()
            snapshot = episode.snapshot()
            if episode.closed:
                return snapshot
            snapshot["closed"] = True
            episode.closing = True
        path = self.archive_path(episode.episode_id)
        try:
            if path is not None:
                payload = json.JSONEncoder(ensure_ascii=False).iterencode(snapshot)
                write_atomic(path, payload)
        except BaseException:
            with episode.lock:
                episode.closing = False
            raise
        with self.lock, episode.lock:
            episode.closed = True
            episode.closing = False
            for token, bound in tuple(self.bindings.items()):
                if bound is episode:
                    del self.bindings[token]
                    break
            if path is not None:
                self.episodes.pop(episode.episode_id, None)
            return snapshot
