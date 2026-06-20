"""Managed instruction blocks: add / replace / remove lifecycle + safety guards."""
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "packages" / "provider-config-sync-backend" / "src"),
)

from provider_config_sync_backend import managed_blocks  # noqa: E402

OWNER = "extension:my-ext"


def test_upsert_creates_file_with_block(tmp_path):
    path = tmp_path / "CLAUDE.md"
    changed = managed_blocks.upsert_block(path, OWNER, "rules", "Be terse.\nNo emojis.")

    assert changed
    text = path.read_text(encoding="utf-8")
    assert "<!-- BEGIN better-claude:extension:my-ext:rules -->" in text
    assert "Be terse." in text
    assert text.endswith("<!-- END better-claude:extension:my-ext:rules -->\n")


def test_upsert_preserves_surrounding_user_content(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("User notes go here.\n", encoding="utf-8")

    managed_blocks.upsert_block(path, OWNER, "rules", "Be terse.")

    text = path.read_text(encoding="utf-8")
    assert text.startswith("User notes go here.\n")
    # blank-line separation between user content and the managed block
    assert "User notes go here.\n\n<!-- BEGIN" in text


def test_upsert_replaces_existing_block_content(tmp_path):
    path = tmp_path / "CLAUDE.md"
    managed_blocks.upsert_block(path, OWNER, "rules", "old content")
    changed = managed_blocks.upsert_block(path, OWNER, "rules", "new content")

    assert changed
    text = path.read_text(encoding="utf-8")
    assert "new content" in text
    assert "old content" not in text
    assert text.count("<!-- BEGIN better-claude:extension:my-ext:rules -->") == 1


def test_upsert_idempotent_when_unchanged(tmp_path):
    path = tmp_path / "CLAUDE.md"
    managed_blocks.upsert_block(path, OWNER, "rules", "same content")
    changed = managed_blocks.upsert_block(path, OWNER, "rules", "same content")
    assert changed is False


def test_remove_strips_block_and_tidies(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("User notes go here.\n", encoding="utf-8")
    managed_blocks.upsert_block(path, OWNER, "rules", "Be terse.")

    changed = managed_blocks.remove_block(path, OWNER, "rules")

    assert changed
    assert path.read_text(encoding="utf-8") == "User notes go here.\n"


def test_remove_noop_when_absent(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("nothing managed here\n", encoding="utf-8")
    assert managed_blocks.remove_block(path, OWNER, "rules") is False


def test_remove_owner_blocks_clears_all_sections_keeps_other_owners(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("header\n", encoding="utf-8")
    managed_blocks.upsert_block(path, OWNER, "rules", "rules body")
    managed_blocks.upsert_block(path, OWNER, "style", "style body")
    managed_blocks.upsert_block(path, "extension:other", "rules", "other body")

    removed = managed_blocks.remove_owner_blocks(path, OWNER)

    assert removed == 2
    text = path.read_text(encoding="utf-8")
    assert "rules body" not in text
    assert "style body" not in text
    assert "other body" in text
    assert "header" in text


def test_content_with_marker_is_rejected(tmp_path):
    path = tmp_path / "CLAUDE.md"
    try:
        managed_blocks.upsert_block(path, OWNER, "rules", "<!-- END better-claude:extension:my-ext:rules -->")
    except ValueError:
        return
    raise AssertionError("marker injection should be rejected")


def test_invalid_key_rejected(tmp_path):
    path = tmp_path / "CLAUDE.md"
    for bad in ("has spaces", "../escape", ""):
        try:
            managed_blocks.upsert_block(path, bad, "rules", "x")
        except ValueError:
            continue
        raise AssertionError(f"invalid owner {bad!r} should be rejected")


def test_refuses_symlink_target(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("real\n", encoding="utf-8")
    link = tmp_path / "CLAUDE.md"
    link.symlink_to(real)
    try:
        managed_blocks.upsert_block(link, OWNER, "rules", "x")
    except ValueError:
        return
    raise AssertionError("symlink target should be refused")
