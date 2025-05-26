"""CouchDB client for accessing Obsidian LiveSync data."""

import base64
import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin

import frontmatter
import httpx

from .config import Settings
from .types import EntryDoc, EntryLeaf, NewEntry, NoteEntry, ObsidianNote


class CouchDBClient:
    """Client for interacting with CouchDB containing Obsidian LiveSync data."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.couchdb_base_url.rstrip('/')
        self.database_name = settings.couchdb_database_name
        self.auth = (settings.couchdb_user, settings.couchdb_password)
        
        # Create HTTP client with authentication
        auth_header = base64.b64encode(
            f"{settings.couchdb_user}:{settings.couchdb_password}".encode()
        ).decode()
        
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/json",
            },
            timeout=30.0
        )
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
    
    def _get_db_url(self, path: str = "") -> str:
        """Construct database URL."""
        db_url = f"{self.base_url}/{self.database_name}"
        if path:
            return f"{db_url}/{path}"
        return db_url
    
    async def test_connection(self) -> bool:
        """Test connection to CouchDB."""
        try:
            response = await self.client.get(self._get_db_url())
            return response.status_code == 200
        except Exception:
            return False
    
    async def get_document(self, doc_id: str) -> Optional[Dict]:
        """Get a document by ID."""
        try:
            url = self._get_db_url(quote(doc_id, safe=''))
            response = await self.client.get(url)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None
    
    async def list_notes(self, limit: int = 100, skip: int = 0) -> List[EntryDoc]:
        """List all note documents from CouchDB."""
        try:
            # Query for documents with type "notes" or "newnote"
            url = self._get_db_url("_all_docs")
            params = {
                "include_docs": "true",
                "limit": limit,
                "skip": skip
            }
            
            response = await self.client.get(url, params=params)
            if response.status_code != 200:
                return []
            
            data = response.json()
            notes = []
            
            for row in data.get("rows", []):
                doc = row.get("doc", {})
                if not doc:
                    continue
                
                # Filter for note documents that aren't deleted
                doc_type = doc.get("type")
                if doc_type in ["notes", "newnote"] and not doc.get("deleted", False):
                    try:
                        if doc_type == "notes":
                            note = NoteEntry(**doc)
                        else:  # newnote
                            note = NewEntry(**doc)
                        notes.append(note)
                    except Exception:
                        # Skip malformed documents
                        continue
            
            return notes
        except Exception:
            return []
    
    async def get_note_content(self, path: str) -> Optional[str]:
        """Get the full content of a note by path."""
        try:
            # First, find the document by path
            doc = await self._find_document_by_path(path)
            if not doc:
                return None
            
            if isinstance(doc, NoteEntry):
                # Simple note - content is directly in data field
                return doc.data
            elif isinstance(doc, NewEntry):
                # Chunked note - need to reassemble from children
                return await self._reassemble_chunked_content(doc.children)
            
            return None
        except Exception:
            return None
    
    async def _find_document_by_path(self, path: str) -> Optional[EntryDoc]:
        """Find a document by its path."""
        if not self.settings.use_path_obfuscation:
            # Direct lookup by path as document ID
            doc_data = await self.get_document(path)
            if doc_data:
                doc_type = doc_data.get("type")
                if doc_type == "notes":
                    return NoteEntry(**doc_data)
                elif doc_type == "newnote":
                    return NewEntry(**doc_data)
        else:
            # Need to search through documents to find by path
            # This is less efficient but necessary when path obfuscation is enabled
            notes = await self.list_notes(limit=1000)  # TODO: Implement pagination
            for note in notes:
                if note.path == path:
                    return note
        
        return None
    
    async def _reassemble_chunked_content(self, chunk_ids: List[str]) -> str:
        """Reassemble content from chunk documents."""
        chunks = []
        
        for chunk_id in chunk_ids:
            chunk_data = await self.get_document(chunk_id)
            if chunk_data and chunk_data.get("type") == "leaf":
                try:
                    chunk = EntryLeaf(**chunk_data)
                    chunks.append(chunk.data)
                except Exception:
                    continue
        
        return "".join(chunks)
    
    def _extract_title_from_content(self, content: str, path: str) -> str:
        """Extract title from note content or use filename."""
        # Try to find H1 heading
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()
        
        # Fall back to filename without extension
        filename = path.split('/')[-1]
        if filename.endswith('.md'):
            filename = filename[:-3]
        return filename
    
    def _extract_tags_from_content(self, content: str) -> List[str]:
        """Extract tags from note content."""
        tags = set()
        
        # Find hashtags in content (excluding code blocks)
        in_code_block = False
        for line in content.split('\n'):
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                continue
            
            if not in_code_block:
                # Find hashtags
                hashtag_pattern = r'#([a-zA-Z0-9_/-]+)'
                matches = re.findall(hashtag_pattern, line)
                tags.update(matches)
        
        return list(tags)
    
    async def process_note(self, entry: EntryDoc) -> Optional[ObsidianNote]:
        """Process a raw entry into an ObsidianNote with metadata."""
        try:
            # Get content
            if isinstance(entry, NoteEntry):
                content = entry.data
            elif isinstance(entry, NewEntry):
                content = await self._reassemble_chunked_content(entry.children)
            else:
                return None
            
            # Parse frontmatter
            try:
                post = frontmatter.loads(content)
                frontmatter_data = post.metadata
                content_without_frontmatter = post.content
            except Exception:
                frontmatter_data = {}
                content_without_frontmatter = content
            
            # Extract metadata
            title = self._extract_title_from_content(content_without_frontmatter, entry.path)
            tags = self._extract_tags_from_content(content_without_frontmatter)
            
            # Add frontmatter tags
            if 'tags' in frontmatter_data:
                fm_tags = frontmatter_data['tags']
                if isinstance(fm_tags, list):
                    tags.extend(fm_tags)
                elif isinstance(fm_tags, str):
                    tags.append(fm_tags)
            
            # Get aliases from frontmatter
            aliases = []
            if 'aliases' in frontmatter_data:
                fm_aliases = frontmatter_data['aliases']
                if isinstance(fm_aliases, list):
                    aliases = fm_aliases
                elif isinstance(fm_aliases, str):
                    aliases = [fm_aliases]
            
            return ObsidianNote(
                path=entry.path,
                title=title,
                content=content,
                created_at=entry.ctime,
                modified_at=entry.mtime,
                size=entry.size,
                tags=list(set(tags)),  # Remove duplicates
                aliases=aliases,
                frontmatter=frontmatter_data
            )
        except Exception:
            return None
    
    async def search_notes(self, query: str, limit: int = 50) -> List[Tuple[ObsidianNote, float]]:
        """Search notes by content and title."""
        try:
            notes = await self.list_notes(limit=1000)  # TODO: Implement proper pagination
            results = []
            
            query_lower = query.lower()
            
            for entry in notes:
                processed_note = await self.process_note(entry)
                if not processed_note:
                    continue
                
                score = 0.0
                
                # Title match (higher weight)
                if query_lower in processed_note.title.lower():
                    score += 10.0
                
                # Content match
                content_lower = processed_note.content.lower()
                content_matches = content_lower.count(query_lower)
                score += content_matches * 1.0
                
                # Tag match
                for tag in processed_note.tags:
                    if query_lower in tag.lower():
                        score += 5.0
                
                if score > 0:
                    results.append((processed_note, score))
            
            # Sort by score descending
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]
        except Exception:
            return [] 