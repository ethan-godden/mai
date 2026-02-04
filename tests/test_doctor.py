import httpx
from typer.testing import CliRunner

from mai.cli import app
from mai.config import Settings
from mai.doctor import Check, run_checks


def server(*model_ids: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, text="OK")
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": m} for m in model_ids]})

    return httpx.MockTransport(handler)


def refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


def settings(**llm) -> Settings:
    return Settings(llm=llm)  # type: ignore[arg-type]


async def test_all_ok_when_models_are_served():
    checks = by_name(
        await run_checks(
            settings(fast={"model": "small"}, main={"model": "big"}),
            transport=server("small:latest", "big"),
        )
    )
    assert checks["Inference server"].status == "ok"
    assert checks["Model: fast"].status == "ok"  # matches the ":latest" tag
    assert checks["Model: main"].status == "ok"
    assert checks["Model: heavy"].status == "ok"  # optional
    assert checks["Search: SearXNG"].status == "ok"


async def test_unconfigured_and_missing_models_fail():
    checks = by_name(
        await run_checks(settings(main={"model": "absent"}), transport=server("other"))
    )
    assert checks["Model: fast"].status == "fail"
    assert "not configured" in checks["Model: fast"].detail
    assert checks["Model: main"].status == "fail"
    assert "not found" in checks["Model: main"].detail


async def test_unreachable_server_fails_and_models_are_unverified():
    checks = by_name(
        await run_checks(
            settings(fast={"model": "a"}, main={"model": "b"}),
            transport=httpx.MockTransport(refused),
        )
    )
    assert checks["Inference server"].status == "fail"
    assert checks["Search: SearXNG"].status == "fail"
    assert checks["Model: main"].status == "warn"


async def test_heavy_tier_is_checked_when_configured():
    checks = by_name(
        await run_checks(
            settings(fast={"model": "a"}, main={"model": "a"}, heavy={"model": "huge"}),
            transport=server("a"),
        )
    )
    assert checks["Model: heavy"].status == "fail"


def test_cli_exits_nonzero_on_failure(monkeypatch):
    # Point at a port nothing listens on so the command fails fast and offline.
    monkeypatch.setenv("MAI_LLM__BASE_URL", "http://127.0.0.1:9/v1")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "check(s) failed" in result.stdout


def test_cli_reports_bad_config(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[llm]\nnope = 1\n")
    result = CliRunner().invoke(app, ["doctor", "--config", str(bad)])
    assert result.exit_code == 2


async def test_search_check_is_skipped_when_searxng_is_not_enabled():
    config = Settings(search={"providers": []})  # type: ignore[arg-type]
    checks = by_name(await run_checks(config, transport=server()))
    assert "Search: SearXNG" not in checks
