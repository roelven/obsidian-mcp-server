#!/usr/bin/env python3
"""Test script to verify decryption functionality."""

# import logging # Logging configuration removed

import asyncio
import os
from obsidian_mcp_server.config import Settings
from obsidian_mcp_server.couchdb_client import CouchDBClient
import textwrap
import pytest


@pytest.mark.asyncio
async def test_decryption():
    """Test decryption with the actual vault."""
    # Load settings from environment
    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="test_db",
        couchdb_user="test_user",
        couchdb_password="test_pass",
        api_key="test_key",
        vault_passphrase="test_passphrase"
    )
    
    print(f"Testing connection to: {settings.couchdb_base_url}")
    print(f"Database: {settings.couchdb_database_name}")
    print(f"Passphrase provided: {'Yes' if settings.vault_passphrase else 'No'}")
    
    # Create client
    client = CouchDBClient(settings)
    
    try:
        # Test connection
        print("\n1. Testing CouchDB connection...")
        connected = await client.test_connection()
        print(f"   Connection: {'✓ Success' if connected else '✗ Failed'}")
        
        if not connected:
            print("Cannot proceed without CouchDB connection")
            return
        
        # List some notes
        print("\n2. Listing recent notes...")
        notes = await client.list_notes(limit=5)
        print(f"   Found {len(notes)} notes")
        
        for i, note in enumerate(notes[:3]):
            print(f"   {i+1}. {note.path} (type: {note.type})")
        
        # Try to process a note
        if notes:
            print(f"\n3. Processing first note: {notes[0].path}")
            processed_note = await client.process_note(notes[0])
            
            if processed_note:
                print(f"   Title: {processed_note.title}")
                print(f"   Content length: {len(processed_note.content)} chars")
                print(f"   Content preview: {processed_note.content[:100]}...")
                print(f"   Tags: {processed_note.tags[:5]}")  # First 5 tags
                
                # Check if content was successfully decrypted
                if "[ENCRYPTED CONTENT" in processed_note.content:
                    print("   ⚠️  Content is still encrypted")
                elif "[DECRYPTION FAILED" in processed_note.content:
                    print("   ✗ Decryption failed")
                else:
                    print("   ✓ Content successfully decrypted!")
            else:
                print("   ✗ Failed to process note")

        # --- Test specific note: 1 feb prep with Ida.md ---
        print("\n3.5. Testing specific note: 1 feb prep with Ida.md")
        NOTE_PATH_TO_TEST = "1 feb prep with Ida.md"
        specific_note_doc = await client._find_document_by_path(NOTE_PATH_TO_TEST) # Use the internal finder first

        if specific_note_doc:
            print(f"   ✓ Found note document. Type: {type(specific_note_doc)}, Path: {specific_note_doc.path}")
            
            # Now get its full content using the public method
            print(f"   Fetching full content for {NOTE_PATH_TO_TEST} using get_note_content()...")
            content = await client.get_note_content(NOTE_PATH_TO_TEST)
            
            if content:
                print(f"   ✓ Content obtained. Preview (first 300 chars):")
                # Ensure content is a string before slicing and printing
                content_str = str(content) 
                print(textwrap.indent(textwrap.shorten(content_str, width=300, placeholder="... (truncated)"), "     "))

                if "[ENCRYPTED CONTENT" in content_str:
                    print("   ⚠️  Full content is still encrypted")
                elif "[DECRYPTION FAILED" in content_str:
                    print("   ✗ Full content decryption failed (contains failure markers)")
                elif not content_str.strip():
                     print("   ? Full content is empty or whitespace.")
                elif content_str.startswith("---"): # Good sign for decrypted Markdown
                    print("   ✓ Full content appears successfully decrypted!")
                else: # Non-empty, not an error, but not starting with --- (could be OK if note has no frontmatter)
                    print(f"   ? Full content retrieved, but does not start with '---'. Actual start: '{content_str[:20]}...'")
            else:
                print(f"   ✗ Failed to get full content for {NOTE_PATH_TO_TEST} using get_note_content()")
        else:
            print(f"   ✗ Failed to find specific note document for path: {NOTE_PATH_TO_TEST} using _find_document_by_path")
        # --- End of specific note test ---
        
        # --- ADD FOCUSED CHUNK DECRYPTION TEST HERE ---
        print("\n3.6. Testing specific chunk decryption for 'UP Intros - Tedy.md'...")
        # Replace with an actual chunk ID from your logs for this note
        # e.g., the first one from your log: "h:+5v3ou6aumsx9"
        TEST_CHUNK_ID = "h:+5v3ou6aumsx9" # MAKE SURE THIS IS A VALID CHUNK ID FOR THE NOTE
        
        if not settings.vault_passphrase:
            print(f"   SKIPPING: Vault passphrase not provided.")
        else:
            print(f"   Attempting to fetch and decrypt chunk: {TEST_CHUNK_ID}")
            chunk_doc_data = await client.get_document(TEST_CHUNK_ID)
            
            if not chunk_doc_data:
                print(f"   ✗ FAILED to fetch chunk document for ID: {TEST_CHUNK_ID}")
            elif chunk_doc_data.get("type") != "leaf":
                print(f"   ✗ Document {TEST_CHUNK_ID} is not of type 'leaf'. Actual type: {chunk_doc_data.get('type')}")
            else:
                raw_chunk_data = chunk_doc_data.get("data")
                if not raw_chunk_data:
                    print(f"   ✗ Chunk {TEST_CHUNK_ID} has no 'data' field or it's empty.")
                else:
                    print(f"   Raw chunk data (first 50 chars): {raw_chunk_data[:50]}")
                    # Directly use the encryption module's try_decrypt
                    from obsidian_mcp_server.encryption import try_decrypt as encryption_try_decrypt
                    decrypted_chunk_content = encryption_try_decrypt(raw_chunk_data, settings.vault_passphrase)
                    
                    if decrypted_chunk_content is not None:
                        print(f"   ✓ SUCCESS: Chunk {TEST_CHUNK_ID} decrypted directly.")
                        print(f"   Decrypted content (first 50 chars): {decrypted_chunk_content[:50]}")
                    else:
                        print(f"   ✗ FAILED: try_decrypt returned None for chunk {TEST_CHUNK_ID}.")
        # --- End of focused chunk decryption test ---
        
        # Test search
        print("\n4. Testing search functionality...")
        search_results = await client.search_notes("test", limit=3)
        print(f"   Found {len(search_results)} search results")
        
        for i, (note, score) in enumerate(search_results):
            print(f"   {i+1}. {note.title} (score: {score:.1f})")
            if "[ENCRYPTED CONTENT" in note.content:
                print("      ⚠️  Content is encrypted")
            elif "[DECRYPTION FAILED" in note.content:
                print("      ✗ Decryption failed")
            else:
                print("      ✓ Content decrypted")
    
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await client.close()


async def focused_decryption_test(client, passphrase):
    # ... the test you've shown the output for ...
    print("3. Attempting to decrypt raw chunk data directly using encryption.decrypt...")
    # ... calls encryption.decrypt directly ...


async def full_note_processing_test(client):
    print("\n--- B. Full Note Processing Test ---")
    note_path_to_test = "1 feb prep with Ida.md"
    print(f"Fetching content for: {note_path_to_test} using client.get_note_content()")
    content = await client.get_note_content(note_path_to_test) # <--- THIS IS THE KEY CALL
    if content:
        print(f"   ✓ Content obtained. Preview (first 200 chars):")
        print(textwrap.shorten(content, width=200, placeholder="..."))
        if "DECRYPTION FAILED" in content or "[ENCRYPTED" in content:
            print("   ✗ ERROR: Content indicates decryption failure or is still encrypted.")
        elif content.startswith("---"): # Assuming decrypted content starts with frontmatter
            print("   ✓ SUCCESS: Content appears decrypted!")
        else:
            print("   ? UNKNOWN: Content retrieved but not in expected decrypted format.")
    else:
        print(f"   ✗ FAILED to get content for {note_path_to_test}")


async def main():
    # ... setup client ...
    # await focused_decryption_test(client, passphrase) # You've shown this output
    # await full_note_processing_test(client)        # I NEED TO SEE THIS OUTPUT
    pass # main is not used by current script structure

if __name__ == "__main__":
    asyncio.run(test_decryption()) 