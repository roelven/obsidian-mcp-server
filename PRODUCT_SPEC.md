## Product Specification

**Version:** 0.1.0
**Date:** May 26, 2025

### 1. Introduction

**1.1. Purpose**
The Obsidian Model Context Protocol (MCP) Server aims to provide a bridge for AI models (like OpenAI's ChatGPT and Anthropic's Claude) to securely access and interact with a user's notes stored in a self-hosted Obsidian vault. This interaction is facilitated by connecting to the user's existing Obsidian LiveSync setup, which uses a CouchDB instance as its backend.

**1.2. Goals**
*   Enable AI models to interact with the content of a user's Obsidian notes.
*   Provide a secure, API-driven mechanism for this access.
*   Leverage the user's existing LiveSync infrastructure for data retrieval.
*   Offer a self-hosted solution that respects user data privacy.
*   Standardize context provision to LLMs using the [Model Context Protocol](https://modelcontextprotocol.io/introduction) version **2025-03-26**.

**1.3. Target Users**
*   **Primary:** AI language models (e.g., ChatGPT, Claude via their MCP client implementations) that need to access a knowledge base.
*   **Secondary:** The Obsidian user who wishes to grant these AI models access to their notes in a controlled manner.

### 2. User Scenarios / Use Cases (MVP)

*   **Scenario 1: Discovering Available Notes**
    *   As an AI model, I want to browse recent notes with their titles and paths, so I can understand the scope of information available and identify relevant notes for a query.
*   **Scenario 2: Retrieving Specific Note Content**
    *   As an AI model, I want to retrieve the full Markdown content and key metadata (like creation/modification dates, tags, aliases) of a specific note identified by its path, so I can use its information to answer questions or perform tasks.
*   **Scenario 3: Comprehensive Note Search**
    *   As an AI model, I want to perform keyword searches across note titles and content with rich results, so I can find notes relevant to a particular topic even if I don't know the exact path.

### 3. Core Features (MVP) - Functional View

*   **CF1: MCP Protocol Compliance:** The server implements MCP version 2025-03-26 with stdio and SSE transport support.
*   **CF2: Limited Resource Listing:**
    *   Retrieve a performance-optimized list of recent notes (maximum 10 items).
    *   For each note, provide:
        *   Unique MCP URI (mcp-obsidian://vault-id/path).
        *   Note title (derived from filename or content).
        *   Relative path.
        *   MIME type (text/markdown).
*   **CF3: Full Note Content Retrieval:**
    *   Fetch the complete Markdown content of any note by its MCP URI.
    *   Handle chunked notes by reassembling content from multiple CouchDB documents.
    *   Support encrypted notes with proper decryption.
*   **CF4: Smart Note Tools:**
    *   **find_notes** – a unified tool that combines searching and browsing. Supports keyword queries, optional `since_days` filter, sorting, existence-only checks (`exists_only`), and automatic full-content inclusion when ≤3 results. Returns up to 50 structured JSON objects with rich metadata (tags, aliases, timestamps, truncated content).
    *   **summarise_note** – generates a concise summary of a single note given its MCP URI, with a configurable `max_words` budget.
    *   Both tools return structured JSON payloads to eliminate client-side parsing overhead.
*   **CF5: Dockerized Deployment:** The server is packaged as a Docker container for ease of deployment and management.
*   **CF6: Configurability:** Key operational parameters (CouchDB connection details, encryption settings) are configurable by the user.

### 4. Architecture Design Decisions

**4.1. Performance-Optimized Resource Listing**
*   **Decision**: Limit `resources/list` to 10 recent notes instead of implementing pagination.
*   **Rationale**: Prevents overwhelming AI clients with large lists while maintaining fast response times.
*   **Alternative**: Comprehensive note discovery is provided through the single `find_notes` tool.

**4.2. Tools-Based Search Implementation**
*   **Decision**: Implement note operations via **two** MCP tools (`find_notes`, `summarise_note`) instead of several overlapping ones.
*   **Rationale**: 
    *   Provides rich search/browse functionality with flexible parameters in a single surface area.
    *   Reduces cognitive load for tool-calling agents by presenting one canonical way to locate notes.
*   **Trade-off**: Search results are not directly accessible via standard MCP resource URIs without an additional `resources/read` call.

### 5. Success Criteria (MVP)

*   AI models can successfully connect to and communicate with the MCP server using standard MCP clients.
*   The server can reliably connect to the user's configured LiveSync (CouchDB) instance.
*   The limited resource listing returns accurate note information for recent notes.
*   The **find_notes** tool provides comprehensive access to the full vault with rich metadata, and **summarise_note** offers server-side summarisation.
*   Note content retrieval returns correct and complete Markdown content, including proper handling of chunked and encrypted notes.
*   The server runs stably as a Docker container using user-provided configurations.

### 6. Future Product Enhancements (High-Level)

*   **Write Operations:** Allow AI models to create new notes, update existing notes, or append content (with appropriate user permissions and safeguards).
*   **Custom HTTP API:** Implement custom HTTP endpoints with Bearer Token authentication for advanced deployment scenarios.
*   **Advanced Search:**
    *   Full-text search capabilities with better ranking algorithms.
    *   Search by specific tags, frontmatter properties, date ranges.
    *   Graph-based queries (e.g., find notes linked to/from a specific note).
*   **Real-time Updates:** 
    *   Implement MCP resource subscriptions for real-time note change notifications.
    *   Leverage CouchDB's `_changes` feed for live updates.
*   **Handling Attachments/Embeds:** Provide capabilities to list and retrieve attachments (images, PDFs, etc.) linked in notes.
*   **User Interface:** A simple web UI for MCP server configuration, connection testing, and monitoring.
*   **Deeper Obsidian Feature Support:**
    *   Enhanced backlink resolution and graph traversal.
    *   Support for Obsidian-specific features like block references and embeds.
*   **Granular Permissions:** More sophisticated access control for multiple AI clients or users.

Claude: "List notes" → CouchDB query (50 docs, sorted) → Process 50 → Return 50
Claude: "Search X" → CouchDB regex search → Process matches only → Return results

---

## Technical Specification

**Version:** 0.1.0
**Date:** May 26, 2025

### 1. System Architecture

A high-level overview of the system:

\`\`\`
[Obsidian Clients (Laptop, iOS, etc.)]
      ^
      | (Obsidian LiveSync via CouchDB)
      v
[Self-Hosted LiveSync (CouchDB Instance, e.g., vault.w22.io)]
      ^
      | (CouchDB API Access - Read Operations)
      v
[Obsidian MCP Server (Docker Container, Python)] 
      ^
      | (MCP Protocol - stdio/SSE transports)
      v
[AI Clients (ChatGPT, Claude via MCP)]
      ^
      | (Configuration: MCP Server connection)
      v
[User]
\`\`\`

*   **Obsidian Clients:** User's Obsidian instances on various devices.
*   **Self-Hosted LiveSync (CouchDB):** The central CouchDB database (e.g., running in Docker on \`https://vault.w22.io\`) that Obsidian LiveSync uses to store and synchronize notes.
*   **Obsidian MCP Server:** A server application (running in a Docker container) that acts as a client to the CouchDB instance. It exposes MCP protocol capabilities for AI models to access note data.
*   **AI Clients:** External services like ChatGPT or Claude that consume the MCP Server's capabilities through their respective MCP client implementations.

**Interaction Flow (Read Operations - MCP/JSON-RPC):**
1.  MCP Client sends an \`initialize\` JSON-RPC request to the MCP Server.
2.  MCP Server responds with its capabilities (declaring \`resources\` and \`tools\` support) and \`serverInfo\`.
3.  MCP Client sends an \`initialized\` JSON-RPC notification.
4.  MCP Client sends JSON-RPC requests (e.g., \`resources/list\`, \`resources/read\`, \`tools/call\`) to the MCP Server.
5.  MCP Server validates the JSON-RPC request.
6.  MCP Server translates the MCP request into CouchDB queries.
7.  MCP Server retrieves data from CouchDB (\`EntryDoc\`s).
8.  MCP Server processes CouchDB data:
    *   For \`resources/list\`, maps a limited set of \`EntryDoc\` fields to MCP \`Resource\` objects (uri, name, mimeType, description). Limited to 10 items for performance.
    *   For \`resources/read\`, if the \`EntryDoc\` is chunked (\`type: "newnote"\`), reassembles full content from \`EntryLeaf\` documents.
    *   For \`tools/call\`, provides rich search and browsing capabilities across the full vault.
9. MCP Server sends the JSON-RPC response back to the MCP Client.

### 2. MCP Protocol Implementation

The server implements the Model Context Protocol (MCP) version **2025-03-26** using standard MCP transports.

**2.1. Transport & Message Format**
*   **Protocol:** JSON-RPC 2.0 over MCP standard transports.
*   **Encoding:** UTF-8.
*   **Supported Transports:** 
    *   **stdio**: Standard input/output for direct subprocess communication
    *   **SSE**: Server-Sent Events over HTTP for web-based clients
*   Messages (requests, responses, notifications) adhere to JSON-RPC 2.0 and MCP 2025-03-26 specifications.

**2.2. Authentication & Authorization**
*   **Current Implementation:** No authentication required for MCP protocol messages (standard for stdio transport).
*   **Future Enhancement:** Custom HTTP endpoints with Bearer Token authentication for advanced deployment scenarios.

**2.3. Lifecycle Methods (JSON-RPC)**

**2.3.1. \`initialize\` (Client to Server)**
*   **Description:** Client initiates connection and capability negotiation.
*   **Client \`params\` Example:**
    \`\`\`json
    {
      "protocolVersion": "2025-03-26",
      "capabilities": { /* client capabilities */ },
      "clientInfo": { "name": "AIClientTool", "version": "1.0" }
    }
    \`\`\`
*   **Server \`result\` Example (Obsidian MCP Server):**
    \`\`\`json
    {
      "protocolVersion": "2025-03-26",
      "capabilities": {
        "resources": {},
        "tools": {}
      },
      "serverInfo": {
        "name": "obsidian-mcp-server",
        "version": "0.1.0"
      }
    }
    \`\`\`

**2.3.2. \`notifications/initialized\` (Client to Server)**
*   **Description:** Notification sent by client after successful \`initialize\` response, indicating it's ready for normal operations.
*   **\`params\`:** None (or empty object).

**2.4. Resource Methods (JSON-RPC)**

**2.4.1. \`resources/list\` (Client to Server)**
*   **Description:** Lists a limited set of available resources (Obsidian notes) for performance.
*   **Performance Design:** Returns maximum 10 recent notes to prevent overwhelming AI clients. For comprehensive note discovery, clients should call the \`find_notes\` tool.
*   **\`params\`:** Standard MCP parameters (cursor, filter, rootUri - currently not implemented)
*   **\`result\` (Server Response):** \`ListResourcesResult\`
    \`\`\`json
    {
      "resources": [
        {
          "uri": "mcp-obsidian://vault-id/notes/project-alpha/index.md",
          "name": "index.md",
          "description": "Path: notes/project-alpha/index.md",
          "mimeType": "text/markdown"
        }
        // ... up to 10 Resource objects
      ]
    }
    \`\`\`

**2.4.2. \`resources/read\` (Client to Server)**
*   **Description:** Retrieves the content of a specific resource (Obsidian note).
*   **\`params\`:**
    *   \`uri\` (string, required): The URI of the resource (from \`resources/list\` response or tool output).
*   **\`result\` (Server Response):** \`ReadResourceResult\`
    \`\`\`json
    {
      "contents": [
        {
          "uri": "mcp-obsidian://vault-id/notes/project-alpha/index.md",
          "mimeType": "text/markdown",
          "text": "# Project Alpha Overview\\n\\nThis is the main note for Project Alpha..."
        }
      ]
    }
    \`\`\`

**2.5. Tools Methods (JSON-RPC)**

**2.5.1. \`tools/list\` (Client to Server)**
*   **Description:** Lists available tools for note operations.
*   **\`result\`:** Returns the \`find_notes\` and \`summarise_note\` tools with detailed JSON schemas.

**2.5.2. \`tools/call\` (Client to Server)**
*   **Description:** Executes unified note operations.
*   **Available Tools:**
    *   \`find_notes\`: Search or browse notes with optional \`query\`, \`since_days\`, \`limit\`, \`sort_by\`, \`include_content\`, and \`exists_only\` parameters. Automatically includes full content when ≤3 results unless disabled. Returns structured JSON arrays (or a boolean payload when \`exists_only\` is true).
    *   \`summarise_note\`: Produce a short extract/summary (configurable \`max_words\`) of a single note identified by its MCP URI.
*   **Performance:** \`find_notes\` supports result sets up to 50 items with rich metadata; summarisation is performed server-side to save tokens.
*   **UX Enhancement:** Unified discovery reduces tool-surface complexity; automatic content inclusion removes the need for follow-up \`resources/read\` calls for small results.

**2.6. Error Handling (JSON-RPC)**
*   Uses standard JSON-RPC error response structure.
*   MCP-specific error codes and server-specific errors for CouchDB connectivity, note processing, etc.

### 3. CouchDB Interaction Details

*   **Connection Parameters (Configurable via Environment Variables):**
    *   \`COUCHDB_BASE_URL\`: The full base URL for the CouchDB instance, including any proxy prefixes.
    *   \`COUCHDB_DATABASE_NAME\`: The name of the database used by LiveSync.
    *   \`COUCHDB_USER\`: The username for CouchDB authentication.
    *   \`COUCHDB_PASSWORD\`: The password for CouchDB authentication.
    *   \`USE_PATH_OBFUSCATION\` (boolean, default: \`false\`): Informs the MCP server if LiveSync's path obfuscation is enabled.
    *   \`VAULT_PASSPHRASE\` (string, optional): The passphrase for decrypting encrypted vault content.
*   **Authentication:** The MCP server authenticates to CouchDB using HTTP Basic Authentication.
*   **URI Construction:** MCP resource URIs are formed: \`mcp-obsidian://{VAULT_ID}/{NOTE_PATH_ENCODED}\`.

### 4. Server-Side Processing

*   **Mapping to MCP \`Resource\` (for \`resources/list\`):**
    *   \`uri\`: Constructed, e.g., \`mcp-obsidian://<VAULT_ID_ENCODED>/<NOTE_PATH_ENCODED>\`.
    *   \`name\`: From filename part of \`EntryDoc.path\`.
    *   \`description\`: MVP: \`EntryDoc.path\`. Future: first line of content (requires fetching/parsing content even for list, performance consideration).
    *   \`mimeType\`: \`"text/markdown"\`.
*   **Content Reassembly (for \`resources/read\` with \`type: "newnote"\`):** Fetch all \`EntryLeaf\` docs referenced in \`children\` and concatenate their \`data\` fields.
*   **Smart Content Loading:** For the \`find_notes\` tool, when result sets are small (≤3 notes), full note content is automatically included to eliminate the need for separate \`resources/read\` calls.
*   **Note Summarisation:** The \`summarise_note\` tool returns a concise extract of a single note, avoiding client-side token costs for summarisation tasks.
*   **Frontmatter/Metadata for Client Consumption:** The MCP server itself does *not* inject parsed frontmatter (tags, aliases) or derived titles/dates into the standard fields of \`Resource\` or \`TextResourceContents\` objects returned to the client. The client gets raw Markdown via \`resources/read\`'s \`text\` field and must parse it if it needs structured metadata not covered by basic \`Resource\` fields. (The MCP server internally parses frontmatter to potentially derive a \`description\` for \`resources/list\` or for future advanced search, but doesn't pass it explicitly via standard MCP resource fields).
*   **Timestamp Handling:** \`EntryDoc.ctime\` and \`EntryDoc.mtime\` (ms epoch) are available internally. Not directly part of standard MCP \`Resource\` or \`TextResourceContents\` schema. If needed for client, would require custom extension or client-side parsing of content if dates are in frontmatter.

### 5. Non-Functional Requirements (Technical Focus)

*   **NFR1: Security:**
    *   API communication (AI Client to MCP Server): HTTPS.
    *   MCP Server to CouchDB communication: Should use HTTPS if CouchDB endpoint supports it.
    *   CouchDB Credentials: Handled securely, injected via environment variables.
    *   Input Validation: For all API parameters.
    *   CouchDB Access: If possible, the CouchDB user for the MCP server should have read-only permissions to the database.
*   **NFR2: Dockerization:** The server MUST be packaged as a Docker container.
*   **NFR3: Configurability:** All key parameters (CouchDB details, API keys, \`USE_PATH_OBFUSCATION\`) managed via environment variables.

### 6. Technology Stack

*   **Backend Language/Framework:** Python with the **MCP Python SDK** (Model Context Protocol official SDK) for protocol implementation and server lifecycle management.
*   **MCP Transport Layer:** Built-in MCP transports (stdio, SSE) provided by the SDK.
*   **CouchDB Client Library (Python):** \`httpx\` for direct HTTP API calls to CouchDB with async support.
*   **Markdown Parsing Library (Python):** \`python-frontmatter\` for parsing YAML frontmatter and extracting metadata.
*   **Configuration Management:** Pydantic for loading/validating environment variables and settings.
*   **Encryption Support:** \`octagonal-wheels\` compatible decryption for encrypted vault content (which is also used for [obsidian-livesync](https://github.com/vrtmrz/obsidian-livesync)).
*   **Rate Limiting:** Custom rate limiter implementation to prevent API abuse.

### 6.1. Recent Improvements (v0.1.0)

*   **Tool Consolidation - \`find_notes\`:** Replaced \`search_notes\`, \`browse_notes\`, and \`get_recent_note\` with a single flexible \`find_notes\` tool (unified search, browse, and existence check capabilities).
*   **New Tool - \`summarise_note\`:** Added server-side summarisation helper that returns a short extract of a specified note.
*   **MCP Protocol Compliance:** Removed custom handlers for standard MCP protocol methods (ping, completion, logging) and rely on the built-in SDK capabilities, eliminating KeyError exceptions and improving protocol compliance.
*   **Content Truncation:** Implemented intelligent content truncation (3000 characters) with clear indicators when full content requires a separate \`resources/read\` call.
*   **Enhanced Tool Descriptions:** Updated tool descriptions to clearly communicate automatic content inclusion behavior and UX improvements.

### 7. Deployment Considerations

*   **Docker Image:** Provide a \`Dockerfile\` to build the application image.
*   **Environment Variables:** Document all required environment variables for configuration.
*   **Networking:** Container exposes a port for the MCP API. User maps this to a host port.
*   **SSL/HTTPS for MCP API:** Assume this to be handled by a reverse proxy (e.g., Nginx, Caddy, Traefik) in front of the Docker container. The reverse proxy manages SSL certificates.
*   **CouchDB Accessibility:** MCP server container needs network access to the CouchDB instance.
*   **LiveSync \`usePathObfuscation\` Setting:** If this LiveSync setting is \`true\`, the MCP server needs a reliable way to map the user-friendly \`notePath\` from the API request to the obfuscated CouchDB document \`_id\`. For MVP, assuming \`false\` simplifies this; if \`true\`, a lookup mechanism (e.g., a view in CouchDB or iterating documents) might be needed if direct querying on the \`path\` field is inefficient for finding the obfuscated \`_id\`.

### 8. Key Technical Risks & Open Questions

*   **LiveSync \`usePathObfuscation\`:** The exact impact and preferred handling strategy if this is \`true\` needs confirmation. If \`_id\` is not the path, finding the document by path might require querying a view or scanning (less efficient).
*   **Chunk Reassembly Performance:** For extremely large notes split into many chunks, reassembly might be resource-intensive. Monitor and optimize if necessary.
*   **Search Scalability:** Basic keyword search by fetching and filtering content in the MCP server won't scale well for very large vaults. Future enhancements should leverage CouchDB views or dedicated search indexes.
*   **CouchDB View Availability:** The efficiency of listing and searching notes could be significantly improved if LiveSync already creates useful CouchDB views. If not, the MCP server might need to create its own or rely on less efficient \`_all_docs\` queries with client-side filtering for some operations.

### 9. Future Technical Enhancements

*   **Write Operations:** Implement robust and safe create/update/delete logic that is compatible with LiveSync's data structures and CouchDB's revision system.
*   **Advanced Search:** Design and implement CouchDB views for efficient searching by metadata, tags, and full-text. Consider integrating with CouchDB Lucene (if available) or an external search index.
*   **Attachment Handling:** Investigate how LiveSync stores attachments in CouchDB and design API endpoints if feasible.
*   **WebSockets for Real-time Updates:** Explore using CouchDB's \`_changes\` feed to push updates to connected clients via WebSockets.
*   **Optimized Title/Metadata Caching:** For frequently accessed notes, consider caching derived titles and parsed frontmatter to reduce processing overhead, with appropriate cache invalidation.

**2.6. MCP Protocol Compliance**
*   **Standard Methods:** The server relies on the built-in MCP Python SDK for handling standard protocol methods (ping, completion, logging, etc.) automatically.
*   **Custom Handlers:** Only implements custom handlers for application-specific functionality (resources, tools).
*   **Error Handling:** Uses standard JSON-RPC error response structure with MCP-specific error codes and server-specific errors for CouchDB connectivity, note processing, etc. 