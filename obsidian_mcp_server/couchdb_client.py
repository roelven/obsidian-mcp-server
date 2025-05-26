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
    
    async def list_notes(self, limit: int = 100, skip: int = 0, sort_by: str = "mtime") -> List[EntryDoc]:
        """List note documents from CouchDB using efficient database queries."""
        try:
            # Use CouchDB _find endpoint for efficient querying
            url = self._get_db_url("_find")
            
            # Build query to find notes and newnotes that aren't deleted
            # Include "plain" type for encrypted vaults
            query = {
                "selector": {
                    "$and": [
                        {
                            "type": {
                                "$in": ["notes", "newnote", "plain"]
                            }
                        },
                        {
                            "$or": [
                                {"deleted": {"$exists": False}},
                                {"deleted": False}
                            ]
                        }
                    ]
                },
                "limit": limit * 3,  # Get more docs since we'll filter for .md files
                "skip": skip,
                "sort": [
                    {sort_by: "desc"}  # Sort by modification time (newest first)
                ]
            }
            
            response = await self.client.post(url, json=query)
            if response.status_code != 200:
                # Fallback to _all_docs if _find is not available
                print(f"DEBUG: _find failed with status {response.status_code}, falling back to _all_docs")
                return await self._list_notes_fallback(limit, skip)
            
            data = response.json()
            notes = []
            total_docs = len(data.get("docs", []))
            print(f"DEBUG: _find returned {total_docs} documents")
            
            for doc in data.get("docs", []):
                doc_type = doc.get("type")
                doc_path = doc.get("path", "")
                
                # Filter for markdown files only
                if not doc_path.endswith(".md"):
                    continue
                    
                try:
                    if doc_type == "notes":
                        note = NoteEntry(**doc)
                    elif doc_type == "newnote":
                        note = NewEntry(**doc)
                    elif doc_type == "plain":
                        # Handle encrypted documents - treat as NewEntry for now
                        # since they have children field for chunks
                        note = NewEntry(**doc)
                    else:
                        continue
                    notes.append(note)
                    
                    # Stop when we have enough notes
                    if len(notes) >= limit:
                        break
                except Exception:
                    # Skip malformed documents
                    continue
            
            return notes
        except Exception:
            # Fallback to original method
            return await self._list_notes_fallback(limit, skip)
    
    async def _list_notes_fallback(self, limit: int, skip: int) -> List[EntryDoc]:
        """Fallback method using _all_docs when _find is not available."""
        try:
            url = self._get_db_url("_all_docs")
            params = {
                "include_docs": "true",
                "limit": limit * 3,  # Get more docs since we'll filter
                "skip": skip
            }
            
            response = await self.client.get(url, params=params)
            if response.status_code != 200:
                print(f"DEBUG: _all_docs failed with status {response.status_code}")
                return []
            
            data = response.json()
            notes = []
            total_rows = len(data.get("rows", []))
            print(f"DEBUG: _all_docs returned {total_rows} rows")
            
            for row in data.get("rows", []):
                doc = row.get("doc", {})
                if not doc:
                    continue
                
                # Filter for note documents that aren't deleted and are markdown files
                doc_type = doc.get("type")
                doc_path = doc.get("path", "")
                if (doc_type in ["notes", "newnote", "plain"] and 
                    not doc.get("deleted", False) and 
                    doc_path.endswith(".md")):
                    try:
                        if doc_type == "notes":
                            note = NoteEntry(**doc)
                        elif doc_type == "newnote":
                            note = NewEntry(**doc)
                        elif doc_type == "plain":
                            note = NewEntry(**doc)
                        else:
                            continue
                        notes.append(note)
                        
                        # Stop when we have enough notes
                        if len(notes) >= limit:
                            break
                    except Exception:
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
                    # Check if chunk is encrypted
                    if hasattr(chunk, 'eden') and chunk.eden:
                        # This is an encrypted chunk - we can't read the content
                        return "[ENCRYPTED CONTENT - Cannot read encrypted vault without passphrase]"
                    chunks.append(chunk.data)
                except Exception:
                    continue
        
        content = "".join(chunks)
        # If content is empty but we had chunks, it's likely encrypted
        if not content and chunk_ids:
            return "[ENCRYPTED CONTENT - Cannot read encrypted vault without passphrase]"
        
        return content
    
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
                # Check if content is encrypted
                if hasattr(entry, 'eden') and entry.eden:
                    content = "[ENCRYPTED CONTENT - Cannot read encrypted vault without passphrase]"
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
        """Search notes using database-level querying for efficiency."""
        try:
            # First try database-level text search
            db_results = await self._search_notes_database(query, limit * 2)
            
            if db_results:
                # Process and score the database results
                results = []
                query_lower = query.lower()
                
                for entry in db_results:
                    processed_note = await self.process_note(entry)
                    if not processed_note:
                        continue
                    
                    score = self._calculate_search_score(processed_note, query_lower)
                    if score > 0:
                        results.append((processed_note, score))
                
                # Sort by score and limit results
                results.sort(key=lambda x: x[1], reverse=True)
                return results[:limit]
            
            # Fallback to client-side search if database search fails
            return await self._search_notes_fallback(query, limit)
            
        except Exception:
            # Fallback to client-side search
            return await self._search_notes_fallback(query, limit)
    
    async def _search_notes_database(self, query: str, limit: int) -> List[EntryDoc]:
        """Search notes using CouchDB's text search capabilities."""
        try:
            # Use CouchDB _find with text search
            url = self._get_db_url("_find")
            
            # Build text search query (simplified to avoid regex issues)
            search_query = {
                "selector": {
                    "$and": [
                        {
                            "type": {
                                "$in": ["notes", "newnote", "plain"]
                            }
                        },
                        {
                            "$or": [
                                {"deleted": {"$exists": False}},
                                {"deleted": False}
                            ]
                        }
                    ]
                },
                "limit": limit * 3,  # Get more docs since we'll filter
                "sort": [{"mtime": "desc"}]
            }
            
            response = await self.client.post(url, json=search_query)
            if response.status_code != 200:
                return []
            
            data = response.json()
            notes = []
            query_lower = query.lower()
            
            for doc in data.get("docs", []):
                doc_type = doc.get("type")
                doc_path = doc.get("path", "")
                doc_data = doc.get("data", "")
                
                # Filter for markdown files only
                if not doc_path.endswith(".md"):
                    continue
                
                # Simple text search in path and data
                if (query_lower in doc_path.lower() or 
                    query_lower in doc_data.lower()):
                    try:
                        if doc_type == "notes":
                            note = NoteEntry(**doc)
                        elif doc_type == "newnote":
                            note = NewEntry(**doc)
                        elif doc_type == "plain":
                            note = NewEntry(**doc)
                        else:
                            continue
                        notes.append(note)
                        
                        # Stop when we have enough notes
                        if len(notes) >= limit:
                            break
                    except Exception:
                        continue
            
            return notes
            
        except Exception:
            return []
    
    async def _search_notes_fallback(self, query: str, limit: int) -> List[Tuple[ObsidianNote, float]]:
        """Fallback client-side search when database search is not available."""
        try:
            # Get a reasonable number of recent notes for client-side search
            notes = await self.list_notes(limit=min(200, limit * 4))
            results = []
            
            query_lower = query.lower()
            
            for entry in notes:
                processed_note = await self.process_note(entry)
                if not processed_note:
                    continue
                
                score = self._calculate_search_score(processed_note, query_lower)
                if score > 0:
                    results.append((processed_note, score))
            
            # Sort by score and limit results
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]
            
        except Exception:
            return []
    
    def _calculate_search_score(self, note: ObsidianNote, query_lower: str) -> float:
        """Calculate search relevance score for a note."""
        score = 0.0
        
        # Path/filename match (highest weight)
        if query_lower in note.path.lower():
            score += 15.0
        
        # Title match (high weight)
        if query_lower in note.title.lower():
            score += 10.0
        
        # Content match (moderate weight, but count occurrences)
        content_lower = note.content.lower()
        content_matches = content_lower.count(query_lower)
        score += content_matches * 1.0
        
        # Tag match (high weight)
        for tag in note.tags:
            if query_lower in tag.lower():
                score += 5.0
        
        return score 