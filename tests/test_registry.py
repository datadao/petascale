"""Tests for petascale.detect.registry."""

import pytest

import petascale.detect.registry as registry


def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the module-level _registry with a clean dict for this test."""
    monkeypatch.setattr(registry, "_registry", {})


class TestAlgoDecorator:
    def test_registers_function(self, monkeypatch):
        _fresh_registry(monkeypatch)

        @registry.algo("test_v1")
        def my_fn():
            return 42

        assert "test_v1" in registry._registry
        assert registry._registry["test_v1"] is my_fn

    def test_decorator_returns_original_function(self, monkeypatch):
        _fresh_registry(monkeypatch)

        @registry.algo("test_v1")
        def my_fn():
            return 99

        assert my_fn() == 99

    def test_duplicate_registration_raises(self, monkeypatch):
        _fresh_registry(monkeypatch)

        @registry.algo("dupe")
        def fn1():
            pass

        with pytest.raises(ValueError, match="already registered"):
            @registry.algo("dupe")
            def fn2():
                pass


class TestGet:
    def test_get_registered(self, monkeypatch):
        _fresh_registry(monkeypatch)

        @registry.algo("get_v1")
        def my_fn():
            return "hello"

        assert registry.get("get_v1") is my_fn

    def test_get_unknown_raises_key_error(self, monkeypatch):
        _fresh_registry(monkeypatch)

        with pytest.raises(KeyError, match="Unknown algorithm"):
            registry.get("nonexistent")

    def test_get_unknown_lists_available(self, monkeypatch):
        _fresh_registry(monkeypatch)

        @registry.algo("alpha")
        def fn_a():
            pass

        with pytest.raises(KeyError, match="alpha"):
            registry.get("beta")


class TestAvailable:
    def test_empty_registry(self, monkeypatch):
        _fresh_registry(monkeypatch)
        assert registry.available() == []

    def test_returns_sorted(self, monkeypatch):
        _fresh_registry(monkeypatch)

        @registry.algo("zzz")
        def fn_z():
            pass

        @registry.algo("aaa")
        def fn_a():
            pass

        assert registry.available() == ["aaa", "zzz"]


class TestV1Registration:
    def test_v1_is_registered(self):
        import petascale.detect.pipeline  # noqa: F401 — ensure module is imported
        assert "v1" in registry._registry

    def test_v1_is_callable(self):
        fn = registry.get("v1")
        assert callable(fn)
