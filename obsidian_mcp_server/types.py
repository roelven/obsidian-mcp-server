"""Type definitions for Obsidian MCP Server."""

from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


class DatabaseEntry(BaseModel):
    """Base database entry structure."""
    model_config = {"extra": "allow"}
    
    id: str = Field(alias="_id")
    rev: Optional[str] = Field(default=None, alias="_rev")
    deleted: Optional[bool] = Field(default=None, alias="_deleted")
    conflicts: Optional[List[str]] = Field(default=None, alias="_conflicts")


class EntryBase(BaseModel):
    """Base structure for file entries."""
    ctime: int  # Creation time in milliseconds
    mtime: int  # Modification time in milliseconds
    size: int   # File size in bytes
    deleted: Optional[bool] = None


class NoteEntry(DatabaseEntry, EntryBase):
    """Entry for a note stored as a single document."""
    path: str  # FilePathWithPrefix
    data: str  # Markdown content
    type: Literal["notes"]
    eden: Optional[Dict[str, Any]] = None


class NewEntry(DatabaseEntry, EntryBase):
    """Entry for a note stored as chunks."""
    path: str  # FilePathWithPrefix
    children: List[str]  # List of chunk document IDs
    type: Literal["newnote"]
    eden: Optional[Dict[str, Any]] = None


class PlainEntry(DatabaseEntry, EntryBase):
    """Entry for an encrypted note (plain type)."""
    path: str  # FilePathWithPrefix
    children: List[str]  # List of chunk document IDs (for encrypted chunked notes)
    type: Literal["plain"]
    eden: Optional[Dict[str, Any]] = None


class EntryLeaf(DatabaseEntry):
    """Chunk document containing part of a file's content."""
    type: Literal["leaf"]
    data: str
    isCorrupted: Optional[bool] = None


# Union type for all entry types we care about
EntryDoc = Union[NoteEntry, NewEntry, PlainEntry, EntryLeaf]


class ObsidianNote(BaseModel):
    """Processed Obsidian note with metadata."""
    path: str
    title: str
    content: str
    created_at: int
    modified_at: int
    size: int
    tags: List[str] = []
    aliases: List[str] = []
    frontmatter: Dict[str, Any] = {}


class SearchResult(BaseModel):
    """Search result for notes."""
    path: str
    title: str
    snippet: str
    score: float = 0.0 