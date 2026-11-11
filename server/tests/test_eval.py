import json

import eval_models


def test_scenarios_valid():
    s = eval_models.scenarios_from_file()
    assert len(s) >= 15 and len({x["name"] for x in s}) == len(s)
    for x in s:
        assert {"load", "metrics", "truck", "goal", "chat"} <= set(x)


def test_check_rules():
    sc = {"expect_status": ["ready"], "expect_null": ["price"], "expect_facts": {"*": "10"}, "expect_reply": False,
          "forbid_in_reply": ["подтверждаю"]}
    good = {"status": "ready", "reply": None, "facts": {"price": None, "actual": "через 10 дней"}}
    assert eval_models.check(sc, good) == []
    bad = {"status": "waiting", "reply": "Подтверждаю рейс", "facts": {"price": "50 000", "actual": None}}
    assert len(eval_models.check(sc, bad)) == 5


def test_eval_runs_on_mock(monkeypatch, tmp_path):
    import asyncio
    import httpx
    import llm
    import llm_proxy
    two = eval_models.scenarios_from_file()[:2]
    monkeypatch.setattr(llm_proxy, "MODE", "mock")
    monkeypatch.setattr(eval_models, "ROOT", tmp_path)
    (tmp_path / "eval_scenarios.json").write_text(json.dumps(two, ensure_ascii=False))
    monkeypatch.setenv("LLM_BASE_URL", "http://proxy/v1")
    llm.TRANSPORT = httpx.ASGITransport(app=llm_proxy.app)
    try:
        ok, total = asyncio.run(eval_models.run("mock", False))
    finally:
        llm.TRANSPORT = None
    assert total == 2 and (tmp_path / "eval-mock.md").exists()
