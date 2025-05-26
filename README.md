# Obsidian MCP Server

A Model Context Protocol (MCP) server that provides AI models with access to your Obsidian notes through your existing LiveSync CouchDB setup.

## Features

- **Read-only access** to your Obsidian notes via MCP
- **Seamless integration** with existing Obsidian LiveSync infrastructure
- **Metadata extraction** including frontmatter, tags, and aliases
- **Content reassembly** for chunked notes
- **Docker support** for easy deployment
- **Configurable** via environment variables

## Architecture

```
[AI Clients (ChatGPT, Claude)] 
      ↓ (MCP Protocol)
[Obsidian MCP Server] 
      ↓ (CouchDB API)
[Your LiveSync CouchDB Instance]
      ↓ (LiveSync Protocol)
[Your Obsidian Vaults]
```

## Prerequisites

- A running Obsidian LiveSync CouchDB instance
- CouchDB credentials with read access to your LiveSync database
- Python 3.10+ (if running locally) or Docker

## Quick Start

### Using Docker (Recommended)

1. **Clone and configure:**
   ```bash
   git clone <this-repo>
   cd obsidian-mcp-server
   cp env.example .env
   ```

2. **Edit `.env` with your settings:**
   ```bash
   COUCHDB_BASE_URL=https://your-couchdb-instance.com/secret-path
   COUCHDB_DATABASE_NAME=your-livesync-db-name
   COUCHDB_USER=your-username
   COUCHDB_PASSWORD=your-password
   API_KEY=your-secure-api-key
   ```

3. **Run with Docker Compose:**
   ```bash
   docker-compose up -d
   ```

4. **Test the connection:**
   ```bash
   curl http://localhost:8000/sse
   ```

### Local Development

1. **Install dependencies:**
   ```bash
   pip install -e .
   ```

2. **Set environment variables:**
   ```bash
   export COUCHDB_BASE_URL="https://your-couchdb-instance.com"
   export COUCHDB_DATABASE_NAME="your-db-name"
   export COUCHDB_USER="your-username"
   export COUCHDB_PASSWORD="your-password"
   export API_KEY="your-api-key"
   ```

3. **Run the server:**
   ```bash
   # For stdio transport (direct MCP client connection)
   obsidian-mcp-server --transport stdio
   
   # For SSE transport (HTTP-based)
   obsidian-mcp-server --transport sse --port 8000
   ```

## Configuration

All configuration is done via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `COUCHDB_BASE_URL` | Yes | - | Full URL to your CouchDB instance |
| `COUCHDB_DATABASE_NAME` | Yes | - | Name of your LiveSync database |
| `COUCHDB_USER` | Yes | - | CouchDB username |
| `COUCHDB_PASSWORD` | Yes | - | CouchDB password |
| `API_KEY` | Yes | - | API key for MCP client authentication |
| `SERVER_PORT` | No | 8000 | Port for SSE transport |
| `USE_PATH_OBFUSCATION` | No | false | Whether LiveSync uses path obfuscation |
| `VAULT_ID` | No | default | Identifier for your vault in URIs |

### CouchDB URL Format

Your `COUCHDB_BASE_URL` should include any secret paths or authentication prefixes:

- **Direct CouchDB:** `http://localhost:5984`
- **With Caddy proxy:** `https://vault.example.com/secret-path`
- **Self-hosted LiveSync:** `https://your-domain.com/e=your-secret`

## MCP Client Integration

### Claude Desktop

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/path/to/your/.env",
        "obsidian-mcp-server",
        "--transport", "stdio"
      ]
    }
  }
}
```

### Custom MCP Client

```python
import mcp

# Connect to the server
async with mcp.ClientSession("http://localhost:8000") as session:
    # List available notes
    resources = await session.list_resources()
    
    # Read a specific note
    content = await session.read_resource(resources[0].uri)
    print(content)
```

## API Reference

The server implements the MCP Resource protocol:

### `resources/list`

Lists all available Obsidian notes.

**Response:**
```json
{
  "resources": [
    {
      "uri": "mcp-obsidian://vault-id/path/to/note.md",
      "name": "Note Title",
      "description": "Path: path/to/note.md",
      "mimeType": "text/markdown"
    }
  ]
}
```

### `resources/read`

Reads the content of a specific note.

**Parameters:**
- `uri`: The resource URI from `resources/list`

**Response:**
```json
{
  "contents": [
    {
      "uri": "mcp-obsidian://vault-id/path/to/note.md",
      "mimeType": "text/markdown",
      "text": "# Note Title\n\nNote content..."
    }
  ]
}
```

## Supported Note Features

- ✅ **Frontmatter** - YAML metadata is preserved
- ✅ **Tags** - Both `#hashtags` and frontmatter tags
- ✅ **Aliases** - From frontmatter
- ✅ **Chunked notes** - Automatically reassembled
- ✅ **Markdown content** - Full content with formatting
- ❌ **Attachments** - Not yet supported
- ❌ **Write operations** - Read-only for safety

## Troubleshooting

### Connection Issues

1. **Test CouchDB access:**
   ```bash
   curl -u username:password https://your-couchdb-url/your-database
   ```

2. **Check server logs:**
   ```bash
   docker-compose logs obsidian-mcp-server
   ```

3. **Verify environment variables:**
   ```bash
   docker-compose config
   ```

### Common Problems

- **"Failed to connect to CouchDB"**: Check your URL, credentials, and network access
- **"No notes found"**: Verify your database name and that LiveSync has synced notes
- **"Path obfuscation errors"**: Set `USE_PATH_OBFUSCATION=true` if LiveSync uses it

## Security Considerations

- The server provides **read-only** access to your notes
- Use strong API keys and secure your CouchDB credentials
- Consider running behind a reverse proxy with SSL
- Limit network access to the CouchDB instance
- Regularly rotate your API keys

## Development

### Running Tests

```bash
pytest tests/
```

### Code Quality

```bash
ruff check .
pyright .
```

### Building Docker Image

```bash
docker build -t obsidian-mcp-server .
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Related Projects

- [Obsidian LiveSync](https://github.com/vrtmrz/obsidian-livesync)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Claude Desktop](https://claude.ai/desktop) 