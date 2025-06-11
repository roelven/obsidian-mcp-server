"""CouchDB client for accessing Obsidian LiveSync data."""

import base64
import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin
import logging

import frontmatter
import httpx
import anyio

from .config import Settings
from .encryption import decrypt_eden_content, decrypt_path, is_path_probably_obfuscated, EDEN_ENCRYPTED_KEY, try_decrypt, SALT_OF_PASSPHRASE
from .types import EntryDoc, EntryLeaf, NewEntry, NoteEntry, PlainEntry, ObsidianNote

logger = logging.getLogger(__name__)

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
                "Accept-Encoding": "gzip"
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
                        },
                        {
                            "mtime": {"$exists": True} # Ensure mtime field exists for sorting
                        }
                    ]
                },
                "limit": limit * 3,  # Get more docs since we'll filter for .md files
                "skip": skip,
                "sort": [
                    {sort_by: "desc"}
                ],
            }
            
            response = await self.client.post(url, json=query)
            if response.status_code != 200:
                # Fallback to _all_docs if _find is not available
                # print(f"DEBUG: _find failed with status {response.status_code}, falling back to _all_docs")
                return await self._list_notes_fallback(limit, skip)
            
            data = response.json()
            notes = []
            total_docs = len(data.get("docs", []))
            # print(f"DEBUG: _find returned {total_docs} documents")
            
            for doc in data.get("docs", []):
                doc_type = doc.get("type")
                doc_path = doc.get("path", "")
                
                # Filter for markdown files only (accept files without extension as note)
                if not self._is_markdown_note(doc_path):
                    continue
                    
                try:
                    if doc_type == "notes":
                        note = NoteEntry(**doc)
                    elif doc_type == "newnote":
                        note = NewEntry(**doc)
                    elif doc_type == "plain":
                        # Handle encrypted documents
                        note = PlainEntry(**doc)
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
                # print(f"DEBUG: _all_docs failed with status {response.status_code}")
                return []
            
            data = response.json()
            notes = []
            total_rows = len(data.get("rows", []))
            # print(f"DEBUG: _all_docs returned {total_rows} rows")
            
            for row in data.get("rows", []):
                doc = row.get("doc", {})
                if not doc:
                    continue
                
                # Filter for note documents that aren't deleted and are markdown files
                doc_type = doc.get("type")
                doc_path = doc.get("path", "")
                if (doc_type in ["notes", "newnote", "plain"] and 
                    not doc.get("deleted", False) and 
                    self._is_markdown_note(doc_path)):
                    try:
                        if doc_type == "notes":
                            note = NoteEntry(**doc)
                        elif doc_type == "newnote":
                            note = NewEntry(**doc)
                        elif doc_type == "plain":
                            note = PlainEntry(**doc)
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
            doc = await self._find_document_by_path(path)
            if not doc:
                return None
            
            content_to_return: Optional[str] = None

            if isinstance(doc, NoteEntry):
                raw_content = doc.data
                if self.settings.vault_passphrase and raw_content:
                    decrypted = try_decrypt(raw_content, self.settings.vault_passphrase)
                    if decrypted is not None:
                        content_to_return = decrypted
                    elif raw_content.startswith("|%|") or raw_content.startswith("["):
                        # If decryption failed but it looked like encrypted content
                        content_to_return = f"[DECRYPTION FAILED NOTE: {doc.path}]"
                    else:
                        # Not recognized as encrypted, or passphrase not effective, keep raw
                        content_to_return = raw_content
                else:
                    # No passphrase or no raw content, keep raw (which might be None or empty)
                    content_to_return = raw_content

            elif isinstance(doc, NewEntry):
                # Chunked notes are handled by _reassemble_chunked_content, which includes decryption
                content_to_return = await self._reassemble_chunked_content(doc.children)
            
            elif isinstance(doc, PlainEntry):
                # PlainEntry is also chunked, its content is in children, similar to NewEntry
                if hasattr(doc, 'children') and doc.children:
                    content_to_return = await self._reassemble_chunked_content(doc.children)
                else:
                    # This case should ideally not happen if PlainEntry always means chunked encrypted content.
                    # Handle case where PlainEntry might not have children (e.g. malformed or different interpretation)
                    # print(f"WARNING: PlainEntry {doc.path} has no children. Returning empty content.")
                    content_to_return = f"[NO CONTENT IN PLAINENTRY CHILDREN: {doc.path}]"
            
            return content_to_return
        except Exception as e:
            # Consider logging the exception e properly for server-side diagnostics
            # print(f"Error in get_note_content for {path}: {e}") # For debugging
            # For the client, return a generic error message to avoid leaking details
            return f"[ERROR GETTING CONTENT FOR {path}: Processing error]"
    
    async def _find_document_by_path(self, path: str) -> Optional[EntryDoc]:
        """Find a document by its path."""
        doc_data: Optional[Dict] = None
        
        if not self.settings.use_path_obfuscation:
            # Try path as is
            # DEBUG: print(f"DEBUG _find_document_by_path: Attempting direct lookup (non-obfuscated) for path: {path}")
            doc_data = await self.get_document(path)
            if not doc_data:
                # Try lowercase path if initial attempt failed
                # DEBUG: print(f"DEBUG _find_document_by_path: Direct lookup failed, trying lowercase: {path.lower()}")
                doc_data = await self.get_document(path.lower())
        else:
            # For obfuscated paths, direct lookup with the raw path is unlikely to work.
            # We could try get_document(potentially_obfuscated_id_derived_from_path) if such a derivation exists,
            # but typically we'd rely on iterating and decrypting paths.
            # For now, if obfuscation is on, we skip direct lookup based on `path` string.
            pass # doc_data remains None, will proceed to iteration

        if doc_data:
            doc_type = doc_data.get("type")
            # DEBUG: print(f"DEBUG _find_document_by_path: Direct lookup found doc with type: {doc_type} for id used in lookup")
            try:
                if doc_type == "notes":
                    return NoteEntry(**doc_data)
                elif doc_type == "newnote":
                    return NewEntry(**doc_data)
                elif doc_type == "plain":
                    return PlainEntry(**doc_data)
                # DEBUG: print(f"DEBUG _find_document_by_path: Unknown doc type '{doc_type}'. Returning None.")
                return None 
            except Exception as e: 
                # DEBUG: print(f"DEBUG _find_document_by_path: Direct lookup found doc, but failed to parse: {e}")
                return None 

        # If direct lookup failed, or if path obfuscation is on (in which case direct lookup with raw path is unlikely to work anyway)
        # Fallback to iterating through notes from list_notes
        # This is especially needed if path_obfuscation is true, or if _id != path when obfuscation is false.
        
        # DEBUG: print(f"DEBUG _find_document_by_path: Direct lookup for '{path}' failed or path obfuscation ({self.settings.use_path_obfuscation}) might require iteration. Iterating...")
        
        # The list_notes already sorts by mtime desc, which is good for finding recent notes if that's relevant
        # couchdb_list_limit_for_path_search should be high enough if the note isn't extremely old
        notes_from_list = await self.list_notes(limit=self.settings.couchdb_list_limit_for_path_search)
        # DEBUG: print(f"DEBUG _find_document_by_path: Iterating {len(notes_from_list)} notes after list_notes call for target: {path}")
        
        for note_doc_from_list in notes_from_list:
            current_doc_path = note_doc_from_list.path # This is the 'path' field from the document
            # DEBUG: print(f"DEBUG _find_document_by_path: Comparing target '{path}' with doc path '{current_doc_path}'")

            if self.settings.use_path_obfuscation:
                # DEBUG: print(f"DEBUG _find_document_by_path: Obfuscation ON. Comparing '{path}' with raw '{current_doc_path}' and potentially decrypted.")
                if self.settings.vault_passphrase and is_path_probably_obfuscated(current_doc_path):
                    try:
                        decrypted_path_val = decrypt_path(current_doc_path, self.settings.vault_passphrase + SALT_OF_PASSPHRASE)
                        # DEBUG: print(f"DEBUG _find_document_by_path: Decrypted '{current_doc_path}' to '{decrypted_path_val}'")
                        if decrypted_path_val == path:
                            # DEBUG: print(f"DEBUG _find_document_by_path: Found obfuscated path match: {decrypted_path_val}")
                            return note_doc_from_list
                    except ValueError:
                        # DEBUG: print(f"DEBUG _find_document_by_path: Path decryption failed for {current_doc_path}")
                        continue # Path decryption failed, not a match
                # If obfuscation is on, but path doesn't look obfuscated or no passphrase,
                # we might still compare current_doc_path == path if it's an exact match desired.
                # However, if obfuscation is on, an unencrypted path field matching the target path is ambiguous.
                # Sticking to: if obfuscation is on, we expect to decrypt. If it's not decryptable or doesn't look obfuscated,
                # it's unlikely to be the target unless it's an unencrypted path in an otherwise obfuscated system (edge case).
                # For simplicity, if use_path_obfuscation is true, we primarily rely on finding an obfuscated path that decrypts to the target.
                # A direct match of current_doc_path == path when use_path_obfuscation is true could be added if necessary,
                # but implies some notes might not be path-obfuscated in an obfuscated setup.
            else: # Path obfuscation is OFF
                # DEBUG: print(f"DEBUG _find_document_by_path: Obfuscation OFF. Comparing '{path}' with '{current_doc_path}'")
                if current_doc_path == path:
                    # DEBUG: print(f"DEBUG _find_document_by_path: Found non-obfuscated path match: {current_doc_path}")
                    return note_doc_from_list
        
        # DEBUG: print(f"DEBUG _find_document_by_path: Path '{path}' not found after direct lookup and iteration.")
        return None
    
    async def _reassemble_chunked_content(self, chunk_ids: List[str]) -> str:
        """Reassemble content from chunk documents."""
        logger = logging.getLogger(__name__) # Get logger instance

        logger.debug(f"_reassemble_chunked_content: Attempting to reassemble {len(chunk_ids)} chunks: {chunk_ids}")
        chunks = []
        
        for chunk_id in chunk_ids:
            logger.debug(f"_reassemble_chunked_content: Processing chunk_id: {chunk_id}")
            chunk_data = await self.get_document(chunk_id)
            if chunk_data and chunk_data.get("type") == "leaf":
                try:
                    chunk = EntryLeaf(**chunk_data)
                    chunk_content = chunk.data # Default to raw data
                    logger.debug(f"_reassemble_chunked_content: Chunk {chunk_id} raw data (first 50): {chunk_content[:50] if chunk_content else 'None'}")

                    # Check if chunk is Eden encrypted
                    if hasattr(chunk, 'eden') and chunk.eden and EDEN_ENCRYPTED_KEY in chunk.eden:
                        logger.debug(f"_reassemble_chunked_content: Chunk {chunk_id} identified as Eden encrypted.")
                        if self.settings.vault_passphrase:
                            logger.debug(f"_reassemble_chunked_content: Attempting Eden decryption for {chunk_id}. Passphrase present: True")
                            try:
                                decrypted_eden = decrypt_eden_content(chunk.eden, self.settings.vault_passphrase)
                                chunk_content = decrypted_eden.get("data", "")
                                logger.debug(f"_reassemble_chunked_content: Eden decryption for {chunk_id} successful.")
                            except ValueError: # Decryption failed
                                chunk_content = f"[DECRYPTION FAILED EDEN CHUNK: {chunk_id}]"
                                logger.warning(f"_reassemble_chunked_content: Eden decryption FAILED for chunk {chunk_id}.")
                        else: # Passphrase needed but not provided
                            chunk_content = f"[ENCRYPTED EDEN CHUNK NO PASS: {chunk_id}]"
                            logger.warning(f"_reassemble_chunked_content: Eden chunk {chunk_id} requires passphrase, but none provided.")
                    # If not Eden encrypted (or Eden decryption was skipped/failed), try standard decryption
                    elif self.settings.vault_passphrase:
                        logger.debug(f"_reassemble_chunked_content: Attempting standard decryption for {chunk_id}. Passphrase present: True")
                        decrypted_standard = try_decrypt(chunk.data, self.settings.vault_passphrase)
                        if decrypted_standard is not None:
                            chunk_content = decrypted_standard
                            logger.debug(f"_reassemble_chunked_content: Standard decryption for {chunk_id} successful.")
                        elif chunk.data and (chunk.data.startswith("|%|") or chunk.data.startswith("[") or chunk.data.startswith("%")):
                             chunk_content = f"[DECRYPTION FAILED CHUNK: {chunk_id}]"
                             logger.warning(f"_reassemble_chunked_content: Standard decryption FAILED for chunk {chunk_id}. Data prefix: {chunk.data[:10]}")
                        else: # try_decrypt returned None but not a recognized encrypted prefix
                            chunk_content = chunk.data # Keep raw
                            logger.debug(f"_reassemble_chunked_content: Chunk {chunk_id} not recognized as encrypted or decryption not attempted/failed quietly; kept raw.")
                    else: # No vault_passphrase was provided for standard decryption path
                        logger.debug(f"_reassemble_chunked_content: No passphrase for standard decryption of chunk {chunk_id}. Passphrase present: False")
                        if chunk.data and (chunk.data.startswith("|%|") or chunk.data.startswith("[") or chunk.data.startswith("%")):
                            chunk_content = f"[ENCRYPTED CHUNK NO PASS: {chunk_id}]"
                            logger.warning(f"_reassemble_chunked_content: Standard chunk {chunk_id} requires passphrase, but none provided.")
                        else:
                            chunk_content = chunk.data # Keep raw, likely not encrypted

                    chunks.append(chunk_content)
                except Exception as e:
                    # Log error or handle malformed chunk_data
                    chunks.append(f"[ERROR PROCESSING CHUNK {chunk_id}: {e}]")
            else:
                chunks.append(f"[MISSING CHUNK: {chunk_id}]") # Or handle missing chunk
        
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
            # Get content using the centralized get_note_content method
            # This method handles decryption for all relevant types (NoteEntry, NewEntry, PlainEntry)
            content = await self.get_note_content(entry.path)

            if content is None:
                # If content is None (e.g. doc not found by path, or other critical error in get_note_content before returning a string)
                # It might be better to log this server-side and return None, 
                # as a note without content or path is problematic.
                # For now, matching the test script's expectation if it implies processing notes even if content fetch fails:
                # Let's create a placeholder to indicate missing content, or return None if entry.path is also missing.
                if not entry.path: 
                    # print(f"DEBUG process_note: entry has no path. Entry: {entry}")
                    return None 
                content = "[CONTENT NOT AVAILABLE]"
            
            # Parse frontmatter from the (potentially decrypted or error-string) content
            try:
                post = frontmatter.loads(content)
                frontmatter_data = post.metadata
                content_without_frontmatter = post.content
            except Exception:
                # If content is an error string (e.g., "[DECRYPTION FAILED...]") or not valid Markdown/frontmatter,
                # treat the whole thing as content_without_frontmatter and empty metadata.
                frontmatter_data = {}
                content_without_frontmatter = content # Keep the error string or original content
            
            # Extract metadata
            # Title extraction should be robust enough to handle error strings in content_without_frontmatter
            title = self._extract_title_from_content(content_without_frontmatter, entry.path)
            # Tag extraction should also be robust
            tags = self._extract_tags_from_content(content_without_frontmatter)
            
            # Add frontmatter tags
            if 'tags' in frontmatter_data:
                fm_tags = frontmatter_data['tags']
                if isinstance(fm_tags, list):
                    tags.extend(fm_tags)
                elif isinstance(fm_tags, str):
                    # Handle space-separated tags or comma-separated tags in frontmatter string
                    tags.extend([t.strip() for t in fm_tags.replace(',', ' ').split() if t.strip()])
            
            # Get aliases from frontmatter
            aliases = []
            if 'aliases' in frontmatter_data:
                fm_aliases = frontmatter_data['aliases']
                if isinstance(fm_aliases, list):
                    aliases = [str(a) for a in fm_aliases if a] # Ensure strings and filter empty
                elif isinstance(fm_aliases, str):
                    aliases = [a.strip() for a in fm_aliases.split(',') if a.strip()] # Comma-separated
            
            return ObsidianNote(
                path=entry.path,
                title=title,
                content=content, # This is the original content string from get_note_content
                created_at=entry.ctime,
                modified_at=entry.mtime,
                size=entry.size, # This might not be accurate if content was decrypted/is error string
                tags=list(set(tags)),  # Remove duplicates
                aliases=aliases,
                frontmatter=frontmatter_data
            )
        except Exception as e:
            # Log e for server-side debugging
            # print(f"Error processing note {entry.path if hasattr(entry, 'path') else 'unknown'}: {e}")
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
        """Optimised search using Mango `$text` operator if a text index exists.
        Falls back to the previous (slower) implementation when `$text` is unavailable.
        """
        try:
            url = self._get_db_url("_find")

            # Attempt fast text search via Mango `$text`.
            text_query = {
                "selector": {
                    "$and": [
                        {"type": {"$in": ["notes", "newnote", "plain"]}},
                        {"$text": query}
                    ]
                },
                "limit": limit,
                # Only pull lightweight fields; we will fetch full content lazily.
                "fields": ["_id", "path", "type", "ctime", "mtime", "size", "children"]
            }

            response = await self.client.post(url, json=text_query)

            # If `$text` search is unavailable (status 400) or any non-200 status, fall back.
            if response.status_code != 200:
                return await self._search_notes_fallback(query, limit)

            # We only have metadata at this point; fetch full doc (still limited) for processing.
            docs_meta = response.json().get("docs", [])
            full_docs: List[EntryDoc] = []

            # Fetch full documents concurrently.
            async with anyio.create_task_group() as tg:
                contents: List[dict] = []

                async def _pull(docid: str):
                    doc = await self.get_document(docid)
                    if doc:
                        contents.append(doc)

                for meta in docs_meta:
                    tg.start_soon(_pull, meta["_id"])

            for doc in contents:
                try:
                    doc_type = doc.get("type")
                    if doc_type == "notes":
                        full_docs.append(NoteEntry(**doc))
                    elif doc_type == "newnote":
                        full_docs.append(NewEntry(**doc))
                    elif doc_type == "plain":
                        full_docs.append(PlainEntry(**doc))
                except Exception:
                    continue

            return full_docs[:limit]

        except Exception:
            # Fallback: previous slower implementation.
            return await self._search_notes_fallback(query, limit)
    
    async def _search_notes_fallback(self, query: str, limit: int) -> List[Tuple[ObsidianNote, float]]:
        """Fallback client-side search when database search is not available."""
        try:
            # We may need to scan multiple pages if the vault is large and the note is old.
            page_size = 200  # sensible chunk
            skip = 0
            gathered: List[Tuple[ObsidianNote, float]] = []
            query_lower = query.lower()

            # hard stop after scanning 5000 docs to avoid runaway latency
            scanned_docs = 0
            max_scan = 5000

            while len(gathered) < limit and scanned_docs < max_scan:
                batch = await self.list_notes(limit=page_size, skip=skip)
                if not batch:
                    break  # no more docs

                for entry in batch:
                    processed_note = await self.process_note(entry)
                    if not processed_note:
                        continue

                    score = self._calculate_search_score(processed_note, query_lower)
                    if score > 0:
                        gathered.append((processed_note, score))
                        if len(gathered) >= limit:
                            break

                scanned_docs += len(batch)
                skip += len(batch)

                if scanned_docs % 1000 == 0:
                    logger.debug("_search_notes_fallback scanned %s docs (matches=%s) so far for query '%s'", scanned_docs, len(gathered), query)

            # sort gathered by score desc
            gathered.sort(key=lambda x: x[1], reverse=True)
            logger.debug("_search_notes_fallback finished query '%s' scanned %s docs total, matches=%s", query, scanned_docs, len(gathered))
            return gathered[:limit]

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

    async def get_recent_note(self, sort_by: str = "mtime") -> Optional[ObsidianNote]:
        """Get the single most recent note with full content."""
        try:
            # Get just one recent note
            entries = await self.list_notes(limit=1, sort_by=sort_by)
            if not entries:
                return None
            
            # Process the note to get full content
            return await self.process_note(entries[0])
        except Exception as e:
            logger.error(f"Error getting recent note: {e}")
            return None 

    def _is_markdown_note(self, path: str) -> bool:
        """Return True if the path looks like a markdown note ('.md' or no extension)."""
        return path.endswith(".md") or "." not in path