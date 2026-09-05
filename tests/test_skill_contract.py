from pathlib import Path


LEGACY_SKILLS = (
    Path("skills/grill-reel/SKILL.md"),
    Path(".agents/plugins/plugins/instagram-creator/skills/grill-reel/SKILL.md"),
)


def test_single_creator_skills_load_profile_before_indexing():
    for skill_path in LEGACY_SKILLS:
        content = skill_path.read_text()
        profile_call = content.index("get_current_creator_profile")
        index_call = content.index("index_video_from_url")

        assert profile_call < index_call
        assert "one creator configured by the server" in content.lower()
        assert "Bearer " not in content


def test_workflow_entrypoints_match_their_bundled_plugin_copies():
    from video_processor.workflow_guides import WORKFLOW_FILES

    for name in WORKFLOW_FILES:
        skill_path = Path("skills") / name / "SKILL.md"
        bundled_path = Path(".agents/plugins/plugins/instagram-creator") / skill_path
        content = skill_path.read_text()

        assert content == bundled_path.read_text()
        assert f'{{"name":"{name}"}}' in content
        assert "get_workflow" in content
        assert "Bearer " not in content


def test_private_plugin_has_no_fake_chatgpt_connection_or_secret():
    plugin_root = Path(".agents/plugins/plugins/instagram-creator")
    manifest = (plugin_root / ".codex-plugin/plugin.json").read_text()

    assert not (plugin_root / ".app.json").exists()
    assert "plugin_asdk_app" not in manifest
    assert "Bearer " not in manifest
