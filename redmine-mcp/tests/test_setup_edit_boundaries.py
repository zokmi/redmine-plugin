"""Real settings flows must edit complete TOML statements, never embedded text."""
import tomllib

import pytest

from redmine_mcp.setup import config_ops, toml_edit, wizard
from tests.test_setup_config_ops import _set_global, _set_site
from tests.test_setup_wizard import _固定回應, _成功驗證, 腳本化提問

GLOBALS = ('download_dir = """\nmax_attachment_mb = 8\n[sites.fake]\n""" # keep\n'
           "# comment\u2028max_attachment_mb = 17\n"
           "upload_dir = '~/old'\n")
SITE_PATH = ('download_dir = \'\'\'\nurl = "decoy"\napi_key = "decoy"\n'
             "[sites.fake]\n''' # unchanged\n")
FIELDS = [
    'url = "https://old"\napi_key = "old-key"\n',
    '  "\\u0075rl" = "https://old"\n\t"api_\\u006bey" = "old-key"\n',
]


@pytest.mark.parametrize("limit", ["", '  "max_attachment_mb" = 9\n'])
def test_actual_global_edit_preserves_embedded_keys(tmp_path, limit):
    target = tmp_path / "config.toml"
    original = GLOBALS + limit + "[sites.main]\n" + FIELDS[0]
    target.write_text(original, encoding="utf-8")
    script = 腳本化提問([], [], [])
    assert _set_global(script, target, max_attachment_mb=25, assume_yes=True) == 0
    content = target.read_text(encoding="utf-8")
    assert tomllib.loads(content)["max_attachment_mb"] == 25
    assert GLOBALS in content
    assert _set_global(script, target, clear=["max_attachment_mb"], assume_yes=True) == 0
    content = target.read_text(encoding="utf-8")
    assert "max_attachment_mb" not in tomllib.loads(content)
    assert GLOBALS in content


@pytest.mark.parametrize("fields", FIELDS)
@pytest.mark.parametrize("header", ["[sites.main]", '  [ "sites" . "ma\\u0069n" ]'])
@pytest.mark.parametrize("description", [None, "new\nmultiline # description"])
def test_actual_site_and_wizard_edits(tmp_path, fields, header, description):
    target = tmp_path / "config.toml"
    target.write_text(GLOBALS + header + "\n" + SITE_PATH + fields
                      + "description = '''first\nsecond''' # note\n", encoding="utf-8")
    script = 腳本化提問([], [], [])
    assert _set_site(script, target, url="https://new", description=description,
                     clear_description=description is None, assume_yes=True) == 0
    content = target.read_text(encoding="utf-8")
    parsed = tomllib.loads(content)["sites"]["main"]
    assert parsed["url"] == "https://new"
    assert parsed.get("description") == description
    assert GLOBALS in content and SITE_PATH in content
    script = 腳本化提問(["https://wizard", "wizard-key", "wizard description"],
                         [True, True], [1, 0])
    assert wizard.run_setup(script.build(), config_path=target, run=_固定回應(0),
                            verify=_成功驗證, finish=False) == 0
    content = target.read_text(encoding="utf-8")
    parsed = tomllib.loads(content)["sites"]["main"]
    assert parsed["url"] == "https://wizard" and parsed["api_key"] == "wizard-key"
    assert parsed["description"] == "wizard description"
    assert GLOBALS in content and SITE_PATH in content


@pytest.mark.parametrize("fields", FIELDS)
def test_converted_statements_remain_editable(tmp_path, fields):
    target = tmp_path / "config.toml"
    target.write_text(toml_edit.convert_legacy(GLOBALS + fields
                      + "description = '''first\nsecond'''\n"), encoding="utf-8")
    script = 腳本化提問([], [], [])
    assert _set_site(script, target, site="default", url="https://converted",
                     clear_description=True, assume_yes=True) == 0
    content = target.read_text(encoding="utf-8")
    assert tomllib.loads(content)["sites"]["default"] == {
        "url": "https://converted", "api_key": "old-key"}
    assert GLOBALS in content


@pytest.mark.parametrize("flow", ["global", "site", "wizard"])
def test_wrong_semantic_edit_never_writes(tmp_path, monkeypatch, flow):
    target = tmp_path / "config.toml"
    original = "[sites.main]\n" + FIELDS[0]
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(toml_edit, "set_key_in", lambda text, *args: text)
    writes = []
    monkeypatch.setattr(config_ops, "write_config", lambda *args: writes.append(args))
    monkeypatch.setattr(wizard, "write_config", lambda *args: writes.append(args))
    script = 腳本化提問([], [], [])
    if flow == "global":
        result = _set_global(script, target, max_attachment_mb=25, assume_yes=True)
    elif flow == "site":
        result = _set_site(script, target, url="https://new", assume_yes=True)
    else:
        script = 腳本化提問(["https://new", "new-key", ""], [True, True], [1, 0])
        result = wizard.run_setup(script.build(), config_path=target, run=_固定回應(0),
                                  verify=_成功驗證, finish=False)
    assert result == 1
    assert not writes and target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("header", ["[sites.main]", '  [ "sites" . "main" ]'])
def test_actual_wizard_replaces_only_real_fields(tmp_path, header):
    target = tmp_path / "config.toml"
    target.write_text(GLOBALS + header + "\n" + SITE_PATH + FIELDS[0], encoding="utf-8")
    script = 腳本化提問(["https://wizard", "wizard-key", ""], [True, True], [1, 0])
    assert wizard.run_setup(script.build(), config_path=target, run=_固定回應(0),
                            verify=_成功驗證, finish=False) == 0
    content = target.read_text(encoding="utf-8")
    parsed = tomllib.loads(content)["sites"]["main"]
    assert parsed["url"] == "https://wizard"
    assert parsed["api_key"] == "wizard-key"
    assert GLOBALS in content and SITE_PATH in content


@pytest.mark.parametrize("existing", [False, True])
def test_wizard_checks_requested_values_at_write_boundary(tmp_path, monkeypatch, existing):
    target = tmp_path / "config.toml"
    original = "[sites.main]\n" + FIELDS[0] if existing else ""
    if existing:
        target.write_text(original, encoding="utf-8")
        monkeypatch.setattr(config_ops, "apply_changes", lambda text, *args: text)
        script = 腳本化提問(["https://new", "new-key", ""], [True, True], [1, 0])
    else:
        monkeypatch.setattr(toml_edit, "render_site_block",
                            lambda *args: "[sites.main]\n" + FIELDS[0])
        script = 腳本化提問(["https://new", "new-key", "main", ""], [True], [])
    writes = []
    monkeypatch.setattr(wizard, "write_config", lambda *args: writes.append(args))
    assert wizard.run_setup(script.build(), config_path=target, run=_固定回應(0),
                            verify=_成功驗證, finish=False) == 1
    assert not writes
