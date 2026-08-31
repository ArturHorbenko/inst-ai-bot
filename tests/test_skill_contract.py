from pathlib import Path


SKILLS = (
    Path("skills/adapt-reel/SKILL.md"),
    Path("skills/grill-reel/SKILL.md"),
    Path(".agents/plugins/plugins/instagram-creator/skills/adapt-reel/SKILL.md"),
    Path(".agents/plugins/plugins/instagram-creator/skills/grill-reel/SKILL.md"),
)


def test_single_creator_skills_load_profile_before_indexing():
    for skill_path in SKILLS:
        content = skill_path.read_text()
        profile_call = content.index("get_current_creator_profile")
        index_call = content.index("index_video_from_url")

        assert profile_call < index_call
        assert "one creator configured by the server" in content.lower()
        assert "Bearer " not in content


def test_private_plugin_has_no_fake_chatgpt_connection_or_secret():
    plugin_root = Path(".agents/plugins/plugins/instagram-creator")
    manifest = (plugin_root / ".codex-plugin/plugin.json").read_text()

    assert not (plugin_root / ".app.json").exists()
    assert "plugin_asdk_app" not in manifest
    assert "Bearer " not in manifest
