from typing import Any

from dbos.cli import _github_init
from dbos.cli._template_init import get_templates_directory
from dbos.cli.cli import _resolve_project_name_and_template


def test_resolve_project_name_and_template() -> None:
    git_templates = ["dbos-toolbox", "dbos-app-starter", "dbos-cron-starter"]
    templates_dir = get_templates_directory()

    # dbos init my-app -t dbos-toolbox
    project_name, template = _resolve_project_name_and_template(
        project_name="my-app",
        template="dbos-toolbox",
        config=False,
        git_templates=git_templates,
        templates_dir=templates_dir,
    )
    assert project_name == "my-app"
    assert template == "dbos-toolbox"

    # dbos init -t dbos-toolbox
    project_name, template = _resolve_project_name_and_template(
        project_name=None,
        template="dbos-toolbox",
        config=False,
        git_templates=git_templates,
        templates_dir=templates_dir,
    )
    assert project_name == "dbos-toolbox"
    assert template == "dbos-toolbox"


def test_github_template_uses_pinned_ref(monkeypatch: Any) -> None:
    requested_urls: list[str] = []

    def fake_fetch(url: str) -> dict[str, list[Any]]:
        requested_urls.append(url)
        return {"tree": []}

    monkeypatch.setattr(_github_init, "_fetch_github", fake_fetch)

    assert _github_init._fetch_github_tree(_github_init.TEMPLATE_REF) == []
    assert requested_urls == [
        f"{_github_init.DEMO_REPO_API}/git/trees/"
        f"{_github_init.TEMPLATE_REF}?recursive=1"
    ]
