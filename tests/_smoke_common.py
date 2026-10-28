"""Shared AstrBot stub for the _smoke_v*.py suite.

Why this module exists
----------------------
Every smoke test used to carry its own 27-58 line copy of the same
astrbot module stub: 33 copies of _install_stub and 2 of
_install_astrbot_stub, in 8 slightly-different variants. A change to the
stub surface (a new decorator, a new attribute) meant editing all of
them, and a missed file only failed when that particular test ran.

install_stub() is the superset of every variant the tests used:

- modules: astrbot, astrbot.api, astrbot.api.star, astrbot.api.event
- Star / register / Context (with get_config) / AstrMessageEvent
- filter with event_message_type / command / on_llm_request /
  on_llm_response
- register() mirrors its version argument onto the decorated class

Deliberately NOT included: astrbot.core.agent.message.TextPart. Two tests
(v70 / v71) install that module themselves because they exercise the
structured-prompt path in handlers/event/inject.py; installing it globally
would silently flip every other test from string concatenation to
TextPart blocks. Use install_textpart() when a test needs it.
"""

import sys
import types


def install_stub():
    """Install the astrbot module surface; return {"version": ...}.

    Idempotent for the tests' purposes: it overwrites the sys.modules
    entries each time, so calling it twice is harmless.
    """
    captured = {}

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    star_mod = types.ModuleType("astrbot.api.star")
    event_mod = types.ModuleType("astrbot.api.event")

    class Star:
        pass

    class Context:
        def get_config(self, *_a, **_k):
            # Some tests construct the plugin with no config at all and
            # rely on the initializer reading defaults through this.
            return {}

    class AstrMessageEvent:
        pass

    class _EventMessageType:
        ALL = "all"

    class _Filter:
        EventMessageType = _EventMessageType

        def event_message_type(self, *_a, **_k):
            def deco(fn):
                return fn
            return deco

        def command(self, *_a, **_k):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def on_llm_request(*_a, **_k):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def on_llm_response(*_a, **_k):
            def deco(fn):
                return fn
            return deco

    def register(*args, **_kwargs):
        # main.py calls register(name, author, desc, version, repo); keep a
        # copy of the version so a test can compare it without importing
        # main's internals.
        if len(args) >= 4:
            captured["version"] = args[3]

        def deco(cls):
            cls._registered_version = captured.get("version")
            return cls

        return deco

    star_mod.Star = Star
    star_mod.register = register
    star_mod.Context = Context
    event_mod.filter = _Filter
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.EventMessageType = _EventMessageType

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.event"] = event_mod
    return captured


def install_textpart():
    """Install astrbot.core.agent.message with a minimal TextPart.

    Only for tests that exercise the structured-prompt path: the plugin
    checks TextPart is not None and falls back to string concatenation
    otherwise, so installing it changes behaviour on purpose.
    """
    core_mod = types.ModuleType("astrbot.core")
    agent_mod = types.ModuleType("astrbot.core.agent")
    message_mod = types.ModuleType("astrbot.core.agent.message")

    class TextPart:
        def __init__(self, text, type="text"):
            self.text = text
            self.type = type
            self._is_temp = False

        def mark_as_temp(self):
            self._is_temp = True
            return self

    message_mod.TextPart = TextPart
    sys.modules["astrbot.core"] = core_mod
    sys.modules["astrbot.core.agent"] = agent_mod
    sys.modules["astrbot.core.agent.message"] = message_mod
    return message_mod
