"""Tests for type definitions."""

import pytest
from obsidian_mcp_server.types import NoteEntry, NewEntry, EntryLeaf, ObsidianNote


def test_note_entry():
    """Test NoteEntry creation and validation."""
    entry = NoteEntry(
        _id="test-note.md",
        path="test-note.md",
        data="# Test Note\n\nThis is a test.",
        type="notes",
        ctime=1640995200000,
        mtime=1640995200000,
        size=100
    )
    
    assert entry.id == "test-note.md"
    assert entry.path == "test-note.md"
    assert entry.type == "notes"
    assert "# Test Note" in entry.data


def test_new_entry():
    """Test NewEntry creation and validation."""
    entry = NewEntry(
        _id="chunked-note.md",
        path="chunked-note.md",
        children=["chunk1", "chunk2"],
        type="newnote",
        ctime=1640995200000,
        mtime=1640995200000,
        size=200
    )
    
    assert entry.id == "chunked-note.md"
    assert entry.path == "chunked-note.md"
    assert entry.type == "newnote"
    assert len(entry.children) == 2


def test_entry_leaf():
    """Test EntryLeaf creation and validation."""
    leaf = EntryLeaf(
        _id="chunk1",
        type="leaf",
        data="This is chunk content."
    )
    
    assert leaf.id == "chunk1"
    assert leaf.type == "leaf"
    assert leaf.data == "This is chunk content."


def test_obsidian_note():
    """Test ObsidianNote creation and validation."""
    note = ObsidianNote(
        path="test-note.md",
        title="Test Note",
        content="# Test Note\n\nContent here.",
        created_at=1640995200000,
        modified_at=1640995200000,
        size=100,
        tags=["test", "example"],
        aliases=["Test"],
        frontmatter={"author": "Test User"}
    )
    
    assert note.path == "test-note.md"
    assert note.title == "Test Note"
    assert "test" in note.tags
    assert "Test" in note.aliases
    assert note.frontmatter["author"] == "Test User" 