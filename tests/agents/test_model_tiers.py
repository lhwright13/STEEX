"""WP7 / C1: per-agent model tiers + pinned pipeline model.

Verifies:
- registry loads an optional `model:` from config/agents.yaml onto AgentConfig
- resolve_model precedence: explicit agents.yaml model > agent_default_model
  (the CLI default model is deliberately never consulted)
- the shipped tier assignments (manager/meta_analysis = opus; data/risk/
  analysis variants/execution/research/learning = sonnet)
- run_agent forwards the resolved model as the `--model` CLI flag
"""

from src.agents.registry import AgentRegistry


def test_agentconfig_reads_model_from_yaml():
    reg = AgentRegistry()
    # Explicit tiers assigned in config/agents.yaml
    assert reg.get_agent("manager").model == "opus"
    assert reg.get_agent("meta_analysis").model == "opus"
    assert reg.get_agent("data").model == "sonnet"
    assert reg.get_agent("execution").model == "sonnet"


def test_resolve_model_prefers_explicit_over_default():
    reg = AgentRegistry()
    # data pins "sonnet" in agents.yaml -> explicit wins over the default
    assert reg.resolve_model("data", "opus") == "sonnet"
    assert reg.resolve_model("manager", "haiku") == "opus"


def test_resolve_model_falls_back_to_default():
    """An agent with no explicit model uses agent_default_model, not the CLI default."""
    reg = AgentRegistry()
    # Inject a modelless agent
    cfg = reg.get_agent("manager")
    cfg.model = None
    assert reg.resolve_model("manager", "opus") == "opus"
    assert reg.resolve_model("manager", "sonnet") == "sonnet"


def test_resolve_model_unknown_agent_uses_default():
    reg = AgentRegistry()
    assert reg.resolve_model("does_not_exist", "sonnet") == "sonnet"


def test_shipped_tiers_match_wp_spec():
    reg = AgentRegistry()
    opus = {"manager", "meta_analysis"}
    sonnet = {
        "data",
        "risk",
        "analysis",
        "analysis_conservative",
        "analysis_aggressive",
        "analysis_momentum",
        "execution",
        "research",
        "learning_agent",
    }
    for name in opus:
        assert reg.get_agent(name).model == "opus", name
    for name in sonnet:
        assert reg.get_agent(name).model == "sonnet", name


def test_run_agent_passes_model_flag(monkeypatch, tmp_path):
    """run_agent must forward `model` as `--model <tier>` to the claude CLI."""
    import src.agents.nodes as nodes
    from src.agents.conclusions import ManagerDecision

    captured = {}

    class _FakeCompleted:
        returncode = 0
        stdout = '{"type":"result","subtype":"success","result":"{}"}\n'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(nodes.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(nodes.subprocess, "run", fake_run)

    class _Settings:
        agent_timeout_seconds = 30
        data_dir = str(tmp_path)

    class _Ctx:
        settings = _Settings()
        project_root = tmp_path
        verbose = False

    nodes.run_agent(
        _Ctx(),
        role="ManagerAgent",
        system_prompt="sys",
        task_message="task",
        conclusion_type=ManagerDecision,
        max_turns=3,
        needs_tools=False,
        model="opus",
    )

    cmd = captured["cmd"]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_run_agent_omits_model_flag_when_none(monkeypatch, tmp_path):
    import src.agents.nodes as nodes
    from src.agents.conclusions import ManagerDecision

    captured = {}

    class _FakeCompleted:
        returncode = 0
        stdout = '{"type":"result","subtype":"success","result":"{}"}\n'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(nodes.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(nodes.subprocess, "run", fake_run)

    class _Settings:
        agent_timeout_seconds = 30
        data_dir = str(tmp_path)

    class _Ctx:
        settings = _Settings()
        project_root = tmp_path
        verbose = False

    nodes.run_agent(
        _Ctx(),
        role="ManagerAgent",
        system_prompt="sys",
        task_message="task",
        conclusion_type=ManagerDecision,
        max_turns=3,
        needs_tools=False,
        model=None,
    )

    assert "--model" not in captured["cmd"]
